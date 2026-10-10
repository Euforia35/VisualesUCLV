"""
main_window.py
--------------
Ventana principal de la app: navegación por el árbol de carpetas del
servidor, búsqueda, favoritos, historial, vista de grilla con carátulas,
descargas y panel de reproducción.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, unquote

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QStackedWidget, QMessageBox,
    QToolButton, QMenu, QFileDialog, QInputDialog,
)

from apache_index import ApacheIndexClient, DirListing, Entry, friendly_error, subtitle_urls_for
from player import PlayerWidget, _fmt_time
from progress import ProgressStore
from thumbnails import (
    ThumbnailManager, candidate_urls_for,
    in_folder_poster_candidates,
    _looks_like_real_image, _revalidate_and_reencode,
)
from downloads import DownloadManager
from movie_detail import MovieDetailView
from music_view import MusicAlbumView, MusicPlayerBar, LyricsDialog
from audio_metadata import AudioMetadataManager
from lyrics import fetch_lyrics
from config_store import ConfigStore
from online_dialogs import ApiKeyDialog, SubtitleSearchDialog
import tmdb
from history_view import HistoryView, COVER_SIZE as HISTORY_COVER_SIZE
from catalog import SiteCatalog
from search_dialog import GlobalSearchDialog
from chromecast_sender import ChromecastSender, CHROMECAST_AVAILABLE, INSTALL_HINT

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "VisualesUCLV"
FAVORITES_FILE = APP_SUPPORT_DIR / "favorites.json"
PROGRESS_FILE = APP_SUPPORT_DIR / "progress.json"
CONFIG_FILE = APP_SUPPORT_DIR / "config.json"
CATALOG_FILE = APP_SUPPORT_DIR / "catalog.json"
THUMBNAILS_DIR = APP_SUPPORT_DIR / "thumbnails_v2"  # v2: caché re-validada con Pillow (ver thumbnails.py)
DOWNLOADS_DIR = Path.home() / "Downloads" / "VisualesUCLV"
PROGRESS_SAVE_INTERVAL_MS = 5000

BASE_URL = "http://visuales.uclv.cu/"

# Accesos directos a las carpetas principales del sitio, mostrados en el
# sidebar. El segundo valor debe coincidir EXACTO con el nombre de la
# carpeta en el servidor (sensible a mayúsculas en muchos servidores Apache).
CATEGORY_SHORTCUTS = [
    ("🎬  Películas", "Peliculas"),
    ("📺  Series", "Series"),
    ("🧸  Infantiles", "Infantiles"),
]

FOLDER_ICON = "📁"
VIDEO_ICON = "🎬"
FILE_ICON = "📄"

GRID_ICON_SIZE = QSize(120, 170)
GRID_CELL_SIZE = QSize(148, 220)


def _fetch_text_bg(session, url: str) -> tuple[str, str]:
    """Descarga un archivo de texto (ej. sinopsis.txt). Corre en un hilo
    común (ThreadPoolExecutor), NO en un QThread — ver nota más abajo
    sobre por qué se cambió."""
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        # Muchos de estos archivos vienen en Latin-1/CP1252 en vez de
        # UTF-8 (típico de servidores viejos), así que probamos UTF-8
        # primero y si falla, caemos a Latin-1 en vez de mostrar texto
        # corrupto con tildes rotas.
        try:
            text = resp.content.decode("utf-8")
        except UnicodeDecodeError:
            text = resp.content.decode("latin-1", errors="replace")
        return text, ""
    except Exception as exc:
        return "", str(exc)


def _clean_title(name: str) -> str:
    """Nombre de carpeta (URL-encoded) -> título legible."""
    name = unquote(name)
    name = name.replace(".", " ").replace("_", " ").strip()
    return " ".join(name.split())


def _parse_size_to_bytes(size_str: str) -> float:
    """Convierte tamaños como "350M", "2.1G", "45K" a bytes (estimado,
    ya que el listado de Apache no da bytes exactos)."""
    size_str = (size_str or "").strip()
    if not size_str or size_str == "-":
        return 0.0
    try:
        unit = size_str[-1].upper()
        mult = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}.get(unit)
        if mult:
            return float(size_str[:-1]) * mult
        return float(size_str)
    except (ValueError, IndexError):
        return 0.0


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# NOTA sobre el cambio de arquitectura de hilos (si estás leyendo esto
# porque algo volvió a crashear): antes, los listados y la sinopsis se
# bajaban con el patrón QThread + moveToThread + worker.run() conectado
# a thread.started. Ese patrón causaba crashes nativos intermitentes en
# macOS (excepciones NSException sin atajar, en medio de la maquinaria
# de PySide para invocar métodos Python desde el hilo de un QThread).
# El patrón que SÍ viene siendo estable en esta app (thumbnails.py,
# downloads.py) es más simple: hilos comunes de Python (vía
# ThreadPoolExecutor) que llaman directamente a `alguna_señal.emit(...)`
# desde el hilo de fondo. Qt detecta automáticamente que el emisor está
# en otro hilo y encola la llamada al slot de forma segura en el hilo
# principal — sin necesitar QThread para nada. navigate_to() y
# _fetch_synopsis() se migraron a este mismo patrón por eso.


def _make_placeholder(entry: Entry) -> QPixmap:
    """Carátula genérica mientras se busca/descarga la real (o si no hay)."""
    pix = QPixmap(GRID_ICON_SIZE)
    pix.fill(QColor("#1E2127") if entry.is_dir else QColor("#242830"))
    painter = QPainter(pix)
    painter.setPen(QColor("#4A4F59"))
    font = QFont()
    font.setPointSize(30)
    painter.setFont(font)
    glyph = "📁" if entry.is_dir else ("🎬" if entry.is_video else "📄")
    painter.drawText(pix.rect(), Qt.AlignCenter, glyph)
    painter.end()
    return pix


class MainWindow(QMainWindow):
    # Señales propias para recibir resultados de trabajo en segundo plano
    # (ver la nota más arriba sobre por qué no se usa QThread para esto).
    _listing_ready_signal = Signal(object, bool)  # (DirListing, push_history)
    _synopsis_ready_signal = Signal(str, str)      # (texto, error)
    _lyrics_ready_signal = Signal(str, str)        # (letra, error)
    _imdb_ready_signal = Signal(str, str, str)     # (sinopsis, url_poster, error)
    _folder_scan_ready_signal = Signal(list, str, float)  # (archivos, nombre_raiz, bytes_totales)
    _chromecast_ready_signal = Signal(bool, str)  # (ok, mensaje)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Visuales UCLV")
        self.resize(1220, 780)

        self.client = ApacheIndexClient(BASE_URL)
        self.history: list[str] = []
        self.history_pos = -1
        self.current_entries: list[Entry] = []
        self.current_url: str | None = None
        self._pending_retry_url: str | None = None
        self._detail_entries: list[Entry] = []
        self._detail_folder_url: str | None = None
        self._detail_poster_key: str | None = None
        self._detail_media_type: str = "movie"
        self._grid_items: dict[str, QListWidgetItem] = {}
        self.view_mode = "list"  # "list" | "grid"
        self._io_executor = ThreadPoolExecutor(max_workers=4)

        self._listing_ready_signal.connect(self._on_listing_ready)
        self._synopsis_ready_signal.connect(self._on_synopsis_ready)
        self._lyrics_ready_signal.connect(self._on_lyrics_ready)
        self._active_lyrics_dialog = None
        self._imdb_ready_signal.connect(self._on_imdb_ready)
        self._folder_scan_ready_signal.connect(self._on_folder_scan_ready)

        APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        self.favorites = self._load_favorites()
        self.progress = ProgressStore(PROGRESS_FILE)
        self.thumbnails = ThumbnailManager(self.client.session, THUMBNAILS_DIR)
        self.thumbnails.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.downloads = DownloadManager(self.client.session, DOWNLOADS_DIR)
        self.downloads.progress.connect(self._on_download_progress)
        self.downloads.status_changed.connect(self._on_download_status)
        self._download_items: dict[str, QListWidgetItem] = {}
        self.audio_metadata = AudioMetadataManager(self.client.session)
        self.audio_metadata.metadata_ready.connect(self._on_track_metadata_ready)
        self._music_folder_poster_key: str | None = None
        self.config = ConfigStore(CONFIG_FILE)
        self.chromecast = ChromecastSender()
        self._chromecast_ready_signal.connect(self._on_chromecast_ready)
        self.catalog = SiteCatalog(self.client, CATALOG_FILE)
        self.catalog.progress.connect(self._on_catalog_progress)
        self.catalog.finished.connect(self._on_catalog_finished)
        self._active_search_dialog: GlobalSearchDialog | None = None
        self._indexing_in_progress = False

        self._build_ui()

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(PROGRESS_SAVE_INTERVAL_MS)
        self._progress_timer.timeout.connect(self._save_current_progress)
        self._progress_timer.start()

        self._show_history()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- Sidebar -----------------------------------------------------
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 20, 16, 20)
        side_layout.setSpacing(6)

        logo = QLabel("VISUALES\nUCLV")
        logo.setObjectName("logo")
        side_layout.addWidget(logo)
        side_layout.addSpacing(18)

        self.history_btn = QPushButton("🕐  Historial")
        self.history_btn.clicked.connect(self._show_history)
        side_layout.addWidget(self.history_btn)

        self.home_btn = QPushButton("🏠  Inicio")
        self.home_btn.clicked.connect(lambda: self.navigate_to(BASE_URL, push_history=True))
        side_layout.addWidget(self.home_btn)

        side_layout.addSpacing(14)
        cat_label = QLabel("CATEGORÍAS")
        cat_label.setObjectName("sectionLabel")
        side_layout.addWidget(cat_label)

        for label, folder in CATEGORY_SHORTCUTS:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, f=folder: self.navigate_to(BASE_URL + f + "/", push_history=True))
            side_layout.addWidget(btn)

        side_layout.addSpacing(14)
        fav_label = QLabel("FAVORITOS")
        fav_label.setObjectName("sectionLabel")
        side_layout.addWidget(fav_label)

        self.fav_list = QListWidget()
        self.fav_list.setObjectName("favList")
        self.fav_list.setMaximumHeight(120)
        self.fav_list.itemDoubleClicked.connect(self._open_favorite)
        side_layout.addWidget(self.fav_list)
        self._refresh_fav_list()

        side_layout.addStretch(1)

        dl_header = QHBoxLayout()
        dl_label = QLabel("DESCARGAS")
        dl_label.setObjectName("sectionLabel")
        dl_header.addWidget(dl_label)
        dl_header.addStretch(1)
        open_folder_btn = QToolButton()
        open_folder_btn.setText("📂")
        open_folder_btn.setToolTip("Abrir carpeta de descargas")
        open_folder_btn.clicked.connect(self._open_downloads_folder)
        dl_header.addWidget(open_folder_btn)
        side_layout.addLayout(dl_header)

        self.downloads_list = QListWidget()
        self.downloads_list.setObjectName("downloadsList")
        self.downloads_list.setMaximumHeight(90)
        self.downloads_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.downloads_list.customContextMenuRequested.connect(self._show_downloads_menu)
        side_layout.addWidget(self.downloads_list)

        root_layout.addWidget(sidebar)

        # --- Panel principal ----------------------------------------------
        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(10)

        top_row = QHBoxLayout()
        self.back_btn = QToolButton()
        self.back_btn.setText("◀")
        self.back_btn.clicked.connect(self.go_back)
        self.fwd_btn = QToolButton()
        self.fwd_btn.setText("▶")
        self.fwd_btn.clicked.connect(self.go_forward)
        self.refresh_btn = QToolButton()
        self.refresh_btn.setText("⟳")
        self.refresh_btn.clicked.connect(self.refresh_current)

        self.breadcrumb = QLabel("")
        self.breadcrumb.setObjectName("breadcrumb")
        self.breadcrumb.setWordWrap(True)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filtrar acá, o Enter para buscar en todo el sitio…")
        self.search_box.textChanged.connect(self._apply_filter)
        self.search_box.returnPressed.connect(self._on_global_search_requested)
        self.search_box.setFixedWidth(260)

        self.global_search_btn = QToolButton()
        self.global_search_btn.setText("🔍")
        self.global_search_btn.setToolTip("Buscar en todo el servidor (no solo esta carpeta)")
        self.global_search_btn.clicked.connect(self._on_global_search_requested)

        self.view_toggle_btn = QPushButton("🖼 Vista grilla")
        self.view_toggle_btn.setCheckable(True)
        self.view_toggle_btn.setToolTip(
            "La vista de grilla descarga carátulas — puede usar más datos"
        )
        self.view_toggle_btn.clicked.connect(self._toggle_view_mode)

        self.fav_btn = QPushButton("☆ Favorito")
        self.fav_btn.clicked.connect(self._toggle_current_favorite)

        top_row.addWidget(self.back_btn)
        top_row.addWidget(self.fwd_btn)
        top_row.addWidget(self.refresh_btn)
        top_row.addSpacing(10)
        top_row.addWidget(self.breadcrumb, stretch=1)
        top_row.addWidget(self.search_box)
        top_row.addWidget(self.global_search_btn)
        top_row.addWidget(self.view_toggle_btn)
        top_row.addWidget(self.fav_btn)
        main_layout.addLayout(top_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        main_layout.addWidget(self.status_label)

        # Vista de listado / reproductor apilados
        self.stack = QStackedWidget()

        self.file_list = QListWidget()
        self.file_list.setObjectName("fileList")
        self.file_list.itemActivated.connect(self._on_item_activated)
        self.file_list.setSpacing(2)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_file_menu)
        self.stack.addWidget(self.file_list)

        self.player = PlayerWidget()
        self.player.error_occurred.connect(self._show_error)
        self.player.track_finished.connect(self._on_video_finished)
        self.player.online_subtitles_requested.connect(self._on_online_subtitles_requested)
        self.stack.addWidget(self.player)

        self.detail_view = MovieDetailView()
        self.detail_view.play_requested.connect(self._on_detail_play_requested)
        self.detail_view.show_files_requested.connect(self._show_raw_file_list)
        self.detail_view.imdb_requested.connect(self._on_imdb_requested)
        self.detail_view.chromecast_requested.connect(self._on_detail_chromecast_requested)
        self.stack.addWidget(self.detail_view)

        self.music_view = MusicAlbumView()
        self.music_view.play_requested.connect(self._on_music_play_requested)
        self.stack.addWidget(self.music_view)

        self.history_view = HistoryView()
        self.history_view.play_requested.connect(self._on_history_play_requested)
        self.history_view.remove_requested.connect(self._on_history_remove_requested)
        self.stack.addWidget(self.history_view)

        main_layout.addWidget(self.stack, stretch=1)

        # Barra de reproducción actual (para volver de la vista de video)
        self.close_player_btn = QPushButton("⬅ Volver al explorador")
        self.close_player_btn.clicked.connect(self._close_player)
        self.close_player_btn.hide()
        main_layout.addWidget(self.close_player_btn)

        # Barra de música persistente: se mantiene visible sin importar
        # qué carpeta estés mirando (como la barra inferior de Spotify).
        self.music_bar = MusicPlayerBar()
        self.music_bar.lyrics_requested.connect(self._on_lyrics_requested)
        main_layout.addWidget(self.music_bar)

        root_layout.addWidget(main_panel, stretch=1)
        self.setCentralWidget(root)

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------
    def navigate_to(self, url: str, push_history: bool, force: bool = False):
        self.status_label.setText("Cargando…")
        self.file_list.clear()
        self._grid_items.clear()
        try:
            self._io_executor.submit(self._fetch_listing_bg, url, force, push_history)
        except RuntimeError:
            pass  # la app se está cerrando; ignoramos trabajo nuevo

    def _fetch_listing_bg(self, url: str, force: bool, push_history: bool):
        """Corre en un hilo del executor. Emitir la señal acá (desde un
        hilo que no es el principal) es seguro: Qt encola automáticamente
        la entrega al slot conectado en el hilo principal."""
        listing = self.client.list_dir(url, force=force)
        self._listing_ready_signal.emit(listing, push_history)

    def _on_listing_ready(self, listing: DirListing, push_history: bool):
        if listing.error:
            self.status_label.setText(friendly_error(listing.error))
            self.file_list.clear()
            retry_item = QListWidgetItem("⟳  Tocá para reintentar")
            retry_item.setData(Qt.UserRole, None)
            self.file_list.addItem(retry_item)
            self._pending_retry_url = listing.url
            return

        self.current_url = listing.url
        self.current_entries = listing.entries
        self.status_label.setText(f"{len(listing.entries)} elementos")

        if push_history:
            # Descarta "adelante" si navegamos a algo nuevo
            self.history = self.history[: self.history_pos + 1]
            self.history.append(listing.url)
            self.history_pos = len(self.history) - 1

        self._update_breadcrumb(listing.url)
        self._update_fav_button()
        self.close_player_btn.hide()

        if self._looks_like_movie_folder(listing.entries, listing.url):
            self._show_movie_detail(listing.entries, listing.url)
        elif self._looks_like_music_folder(listing.entries, listing.url):
            self._show_music_album(listing.entries, listing.url)
        else:
            self._populate_list(listing.entries)
            self.stack.setCurrentWidget(self.file_list)

    # ------------------------------------------------------------------
    # Vista de detalle estilo Netflix
    # ------------------------------------------------------------------
    def _looks_like_movie_folder(self, entries: list[Entry], folder_url: str) -> bool:
        has_video = any(e.is_video for e in entries)
        if not has_video:
            return False
        has_sinopsis = any(e.name.lower() == "sinopsis.txt" for e in entries if not e.is_dir)
        # Todo lo que esté dentro de una carpeta "Peliculas" o "Series"
        # (con o sin tilde) se trata directamente con la vista de
        # detalle, tenga o no sinopsis.txt — más confiable que depender
        # de que todas las carpetas tengan ese archivo.
        path_lower = urlparse(folder_url).path.lower()
        under_peliculas = "/peliculas" in path_lower or "/películas" in path_lower
        under_series = "/series" in path_lower
        return has_sinopsis or under_peliculas or under_series

    def _media_type_for_folder(self, folder_url: str) -> str:
        """"movie" o "tv" — decide qué endpoint de TMDb usar."""
        path_lower = urlparse(folder_url).path.lower()
        return "tv" if "/series" in path_lower else "movie"

    _SEASON_NAME_RE = re.compile(r"^(temporada|season|t\d+|s\d+)\b", re.IGNORECASE)

    def _search_title_for_detail(self, folder_url: str, displayed_title: str) -> str:
        """Para series organizadas como Serie/Temporada 1/, el nombre de
        la carpeta que activa la vista de detalle suele ser la
        temporada, no el nombre de la serie — así que para buscar en
        TMDb usamos la carpeta padre en ese caso."""
        if self._detail_media_type != "tv":
            return displayed_title
        if self._SEASON_NAME_RE.match(displayed_title.strip()):
            parts = [p for p in urlparse(folder_url).path.split("/") if p]
            if len(parts) >= 2:
                return _clean_title(parts[-2])
        return displayed_title

    def _looks_like_music_folder(self, entries: list[Entry], folder_url: str) -> bool:
        has_audio = any(e.is_audio for e in entries)
        if not has_audio:
            return False
        path_lower = urlparse(folder_url).path.lower()
        return "/mp3s" in path_lower or "/mp3" in path_lower

    def _show_movie_detail(self, entries: list[Entry], folder_url: str):
        video_entries = [e for e in entries if e.is_video]
        title = _clean_title([p for p in urlparse(folder_url).path.split("/") if p][-1] if folder_url.strip("/") else "")

        self._detail_media_type = self._media_type_for_folder(folder_url)
        self.detail_view.reset(title or "Sin título")
        size_bits = [e.size for e in video_entries if e.size]
        meta_parts = [f"{len(video_entries)} archivo(s) de video"]
        if len(video_entries) == 1 and video_entries[0].size:
            meta_parts.append(video_entries[0].size)
        self.detail_view.set_meta("  ·  ".join(meta_parts))
        self.detail_view.set_videos([(e.url, e.name) for e in video_entries])
        self.stack.setCurrentWidget(self.detail_view)

        self._detail_entries = entries
        self._detail_folder_url = folder_url

        # Sinopsis (texto) en un hilo aparte
        sinopsis_entry = next((e for e in entries if e.name.lower() == "sinopsis.txt"), None)
        if sinopsis_entry:
            self._fetch_synopsis(sinopsis_entry.url)
        else:
            self.detail_view.set_synopsis("")

        # Carátula (heurística de imágenes dentro de la carpeta)
        self._detail_poster_key = folder_url + "#poster"
        self.thumbnails.request(self._detail_poster_key, in_folder_poster_candidates(entries))

    def _fetch_synopsis(self, url: str):
        try:
            self._io_executor.submit(self._fetch_synopsis_bg, url)
        except RuntimeError:
            pass  # la app se está cerrando; ignoramos trabajo nuevo

    def _fetch_synopsis_bg(self, url: str):
        text, error = _fetch_text_bg(self.client.session, url)
        self._synopsis_ready_signal.emit(text, error)

    def _on_synopsis_ready(self, text: str, error: str):
        self.detail_view.set_synopsis(text if not error else "")

    def _on_detail_play_requested(self, url: str, name: str):
        entry = next((e for e in self._detail_entries if e.url == url), None)
        subs = subtitle_urls_for(entry, self._detail_entries) if entry else []
        self._open_player_with_resume(url, name, subtitle_urls=subs)

    def _on_detail_chromecast_requested(self, url: str, name: str):
        entry = next((e for e in self._detail_entries if e.url == url), None)
        subs = subtitle_urls_for(entry, self._detail_entries) if entry else []
        self._send_to_chromecast(url, name, subtitle_urls=subs)

    def _show_raw_file_list(self):
        self._populate_list(self.current_entries)
        self.stack.setCurrentWidget(self.file_list)

    # ------------------------------------------------------------------
    # Vista de música (carpetas bajo /MP3s/)
    # ------------------------------------------------------------------
    def _show_music_album(self, entries: list[Entry], folder_url: str):
        title = _clean_title(
            [p for p in urlparse(folder_url).path.split("/") if p][-1] if folder_url.strip("/") else ""
        )
        self.music_view.load(entries, title or "Sin título")
        self.stack.setCurrentWidget(self.music_view)

        # Carátula de la carpeta como respaldo inmediato mientras llegan
        # las carátulas embebidas de cada track.
        self._music_folder_poster_key = folder_url + "#musiccover"
        self.thumbnails.request(self._music_folder_poster_key, in_folder_poster_candidates(entries))

        # Metadatos (título/artista/carátula embebida) de cada track, en
        # segundo plano — se van completando fila por fila.
        for entry in entries:
            if entry.is_audio:
                self.audio_metadata.request(entry)

    def _on_track_metadata_ready(self, url: str, track):
        self.music_view.update_track_meta(url, track)
        # si la carpeta no tenía una carátula suelta, usamos la primera
        # carátula embebida que llegue como carátula del álbum
        if track.cover_bytes and self.stack.currentWidget() is self.music_view:
            current_cover = self.music_view.cover_label.pixmap()
            if current_cover is None or current_cover.isNull():
                pix = QPixmap()
                if pix.loadFromData(track.cover_bytes):
                    size = self.music_view.cover_label.size()
                    scaled = pix.scaled(
                        size.width(), size.height(),
                        Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
                    )
                    self.music_view.set_cover_pixmap(scaled)

    def _on_music_play_requested(self, tracks: list, start_index: int, shuffle: bool):
        self.music_bar.play_queue(tracks, start_index=start_index, shuffle=shuffle)

    def _on_lyrics_requested(self, artist: str, title: str):
        dlg = LyricsDialog(artist, title, self)
        dlg.show()
        self._active_lyrics_dialog = dlg
        try:
            self._io_executor.submit(self._fetch_lyrics_bg, artist, title)
        except RuntimeError:
            pass

    def _fetch_lyrics_bg(self, artist: str, title: str):
        text, error = fetch_lyrics(artist, title)
        self._lyrics_ready_signal.emit(text, error)

    def _on_lyrics_ready(self, text: str, error: str):
        dlg = self._active_lyrics_dialog
        if dlg is None:
            return
        try:
            dlg.set_text(text if not error else error)
        except RuntimeError:
            pass  # el usuario ya cerró el diálogo

    # ------------------------------------------------------------------
    # IMDb (vía OMDb) y subtítulos online (vía OpenSubtitles)
    # ------------------------------------------------------------------
    def _ensure_api_key(self, config_key: str, service_name: str, signup_url: str, instructions: str) -> str | None:
        """Devuelve la API key guardada, o pide una nueva con un diálogo
        si todavía no hay ninguna guardada. None si el usuario cancela."""
        key = self.config.get(config_key)
        if key:
            return key
        dlg = ApiKeyDialog(service_name, signup_url, instructions, self)
        if dlg.exec() == ApiKeyDialog.Accepted and dlg.api_key:
            self.config.set(config_key, dlg.api_key)
            return dlg.api_key
        return None

    def _on_imdb_requested(self):
        api_key = self._ensure_api_key(
            "tmdb_api_key", "TMDb",
            "https://www.themoviedb.org/settings/api",
            "Para buscar sinopsis EN ESPAÑOL necesitás una API Key gratuita "
            "de TMDb (The Movie Database). Es instantánea y gratis para uso "
            "personal — elegí \"Developer\" al anotarte.",
        )
        if not api_key:
            return

        displayed_title = self.detail_view.title_label.text()
        search_title = self._search_title_for_detail(self._detail_folder_url or "", displayed_title)
        # si el título tiene un año entre paréntesis, ej. "Interestelar (2014)",
        # lo separamos para que la búsqueda en TMDb sea más precisa
        match = re.search(r"\((\d{4})\)\s*$", search_title)
        year = match.group(1) if match else None
        clean_title = re.sub(r"\s*\(\d{4}\)\s*$", "", search_title).strip()

        tipo = "serie" if self._detail_media_type == "tv" else "película"
        self.detail_view.set_synopsis(f"Buscando \"{clean_title}\" ({tipo}) en TMDb (en español)…")
        try:
            self._io_executor.submit(self._fetch_imdb_bg, api_key, clean_title, year, self._detail_media_type)
        except RuntimeError:
            pass

    def _fetch_imdb_bg(self, api_key: str, title: str, year: str | None, media_type: str):
        plot, poster_url, error = tmdb.fetch_synopsis(api_key, title, year, language="es-ES", media_type=media_type)
        self._imdb_ready_signal.emit(plot, poster_url or "", error)

    def _on_imdb_ready(self, plot: str, poster_url: str, error: str):
        if self.stack.currentWidget() is not self.detail_view:
            return
        if error:
            self.detail_view.set_synopsis(f"TMDb: {error}")
            return
        self.detail_view.set_synopsis(plot)
        # si no hay carátula local, probamos con el póster que trajo TMDb
        if poster_url and self._detail_poster_key:
            self.thumbnails.request(self._detail_poster_key + "#imdb", [poster_url])

    def _on_online_subtitles_requested(self):
        api_key = self._ensure_api_key(
            "opensubtitles_api_key", "OpenSubtitles",
            "https://www.opensubtitles.com/en/consumers",
            "Para buscar subtítulos online necesitás una API Key gratuita "
            "de OpenSubtitles. Registrate como \"Consumer\" en su sitio — "
            "es gratis e instantáneo (la cuenta gratis tiene una cuota "
            "chica de descargas por día).",
        )
        if not api_key:
            return

        default_query = re.sub(r"\.[A-Za-z0-9]+$", "", self.player.current_title() or "")
        default_query = re.sub(r"[._]", " ", default_query).strip()
        dlg = SubtitleSearchDialog(api_key, default_query, self)
        dlg.subtitle_chosen.connect(self.player.add_local_subtitle)
        dlg.exec()

    # ------------------------------------------------------------------
    # Historial ("continuar viendo") — pantalla inicial de la app
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Búsqueda global (todo el servidor, no solo la carpeta actual)
    # ------------------------------------------------------------------
    def _on_global_search_requested(self):
        query = self.search_box.text().strip()

        if self._active_search_dialog is not None:
            # ya hay uno abierto: solo lo traemos al frente y actualizamos
            self._active_search_dialog.raise_()
            self._active_search_dialog.activateWindow()
            return

        dlg = GlobalSearchDialog(initial_query=query, parent=self)
        dlg.item_chosen.connect(self._on_search_result_chosen)
        dlg.search_requested.connect(self._on_search_query)
        dlg.reindex_requested.connect(lambda: self._start_indexing(force=True))
        self._active_search_dialog = dlg

        if self.catalog.entries:
            dlg.set_index_ready(len(self.catalog.entries))
        elif self.catalog.load_from_disk():
            note = " (viejo, puede faltar contenido nuevo — reindexá si querés)" if self.catalog.is_stale() else ""
            dlg.set_index_ready(len(self.catalog.entries))
            dlg.status_label.setText(dlg.status_label.text() + note)
        else:
            self._start_indexing(force=False)

        dlg.exec()
        self._active_search_dialog = None

    def _start_indexing(self, force: bool):
        if self._indexing_in_progress:
            return
        self._indexing_in_progress = True
        try:
            self._io_executor.submit(self._build_catalog_bg)
        except RuntimeError:
            self._indexing_in_progress = False

    def _build_catalog_bg(self):
        self.catalog.build(BASE_URL, root_label="Inicio")

    def _on_catalog_progress(self, count: int, path: str):
        self._indexing_in_progress = True
        dlg = self._active_search_dialog
        if dlg is None:
            return
        try:
            dlg.set_indexing_status(count, path)
        except RuntimeError:
            pass

    def _on_catalog_finished(self, count: int):
        self._indexing_in_progress = False
        dlg = self._active_search_dialog
        if dlg is None:
            return
        try:
            dlg.set_index_ready(count)
        except RuntimeError:
            pass

    def _on_search_query(self, query: str):
        dlg = self._active_search_dialog
        if dlg is None:
            return
        results = self.catalog.search(query)
        label = f"{len(results)} resultado(s)" if query else ""
        try:
            dlg.show_results(results, label)
        except RuntimeError:
            pass

    def _on_search_result_chosen(self, url: str, is_dir: bool):
        target = url if is_dir else url.rsplit("/", 1)[0] + "/"
        self.navigate_to(target, push_history=True)

    def _show_history(self):
        items = self.progress.list_in_progress()
        self.history_view.load(items)
        self.stack.setCurrentWidget(self.history_view)
        self.close_player_btn.hide()
        self.current_url = None
        self.breadcrumb.setText("Historial")

        for item in items:
            try:
                self._io_executor.submit(self._fetch_history_cover_bg, item["url"])
            except RuntimeError:
                pass

    def _fetch_history_cover_bg(self, video_url: str):
        folder_url = video_url.rsplit("/", 1)[0] + "/"
        listing = self.client.list_dir(folder_url)  # cacheado, no siempre pega a la red
        if listing.error:
            return
        candidates = in_folder_poster_candidates(listing.entries)
        if candidates:
            self.thumbnails.request(video_url + "#historycover", candidates)

    def _on_history_play_requested(self, video_url: str, title: str, position: float):
        folder_url = video_url.rsplit("/", 1)[0] + "/"
        listing = self.client.list_dir(folder_url)  # normalmente cacheado (ver arriba)
        subs = []
        if not listing.error:
            entry = next((e for e in listing.entries if e.url == video_url), None)
            if entry:
                subs = subtitle_urls_for(entry, listing.entries)
        self._open_player(video_url, title, start_at=position, subtitle_urls=subs)

    def _on_history_remove_requested(self, video_url: str):
        self.progress.remove(video_url)
        self.history_view.remove_item(video_url)

    # ------------------------------------------------------------------
    # Listado: modo lista / modo grilla con carátulas
    # ------------------------------------------------------------------
    def _toggle_view_mode(self):
        self.view_mode = "grid" if self.view_toggle_btn.isChecked() else "list"
        self.view_toggle_btn.setText(
            "☰ Vista lista" if self.view_mode == "grid" else "🖼 Vista grilla"
        )
        self._apply_view_mode()
        self._populate_list(self.current_entries)

    def _apply_view_mode(self):
        if self.view_mode == "grid":
            self.file_list.setViewMode(QListWidget.IconMode)
            self.file_list.setIconSize(GRID_ICON_SIZE)
            self.file_list.setGridSize(GRID_CELL_SIZE)
            self.file_list.setResizeMode(QListWidget.Adjust)
            self.file_list.setMovement(QListWidget.Static)
            self.file_list.setWordWrap(True)
            self.file_list.setSpacing(8)
        else:
            self.file_list.setViewMode(QListWidget.ListMode)
            self.file_list.setIconSize(QSize(0, 0))
            self.file_list.setGridSize(QSize())
            self.file_list.setSpacing(2)

    def _populate_list(self, entries: list[Entry]):
        self.file_list.clear()
        self._grid_items.clear()

        if self.view_mode == "grid":
            self._apply_view_mode()
            for entry in entries:
                item = QListWidgetItem(self._grid_caption(entry))
                item.setIcon(QIcon(_make_placeholder(entry)))
                item.setData(Qt.UserRole, entry)
                item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
                self.file_list.addItem(item)
                self._grid_items[entry.url] = item

                candidates = candidate_urls_for(entry, entries)
                self.thumbnails.request(entry.url, candidates)
            return

        for entry in entries:
            icon = FOLDER_ICON if entry.is_dir else (VIDEO_ICON if entry.is_video else FILE_ICON)
            extra = f"   {entry.size}" if entry.size else ""
            progress_tag = self._progress_tag(entry)
            item = QListWidgetItem(f"{icon}  {entry.name}{extra}{progress_tag}")
            item.setData(Qt.UserRole, entry)
            self.file_list.addItem(item)

    def _grid_caption(self, entry: Entry) -> str:
        name = entry.name
        if len(name) > 26:
            name = name[:24] + "…"
        return f"{name}{self._progress_tag(entry)}"

    def _progress_tag(self, entry: Entry) -> str:
        if not entry.is_video:
            return ""
        frac = self.progress.fraction(entry.url)
        if frac is None:
            return ""
        return "   ✓ visto" if frac >= 0.95 else f"   ▶ {int(frac * 100)}%"

    def _on_thumbnail_ready(self, key: str, data: bytes):
        # Segunda barrera de seguridad: aunque thumbnails.py ya valida
        # los bytes reales antes de emitir esta señal, no está de más
        # confirmarlo de nuevo acá — pasarle datos que no son una imagen
        # real a QPixmap puede crashear feo en macOS.
        if not _looks_like_real_image(data):
            return

        # ¿Es la carátula de la vista de detalle actual? (incluye el
        # póster alternativo que puede venir de IMDb/OMDb)
        if self._detail_poster_key and key in (self._detail_poster_key, self._detail_poster_key + "#imdb"):
            pix = QPixmap()
            if pix.loadFromData(data):
                scaled = pix.scaled(
                    self.detail_view.POSTER_SIZE[0], self.detail_view.POSTER_SIZE[1],
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
                )
                self.detail_view.set_poster_pixmap(scaled)
            return

        # ¿Es la carátula de un ítem del Historial?
        if key.endswith("#historycover"):
            pix = QPixmap()
            if pix.loadFromData(data):
                scaled = pix.scaled(
                    HISTORY_COVER_SIZE[0], HISTORY_COVER_SIZE[1],
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
                )
                self.history_view.set_cover(key[: -len("#historycover")], scaled)
            return

        item = self._grid_items.get(key)
        if not item:
            return  # el usuario navegó a otra carpeta antes de que llegara
        pix = QPixmap()
        if pix.loadFromData(data):
            scaled = pix.scaled(
                GRID_ICON_SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            item.setIcon(QIcon(scaled))

    def _apply_filter(self, text: str):
        text = text.lower().strip()
        filtered = [e for e in self.current_entries if text in e.name.lower()] if text else self.current_entries
        self._populate_list(filtered)

    def _on_item_activated(self, item: QListWidgetItem):
        entry: Entry | None = item.data(Qt.UserRole)
        if entry is None:
            # Ítem especial de "reintentar" tras un error de carga
            retry_url = getattr(self, "_pending_retry_url", None)
            if retry_url:
                self.navigate_to(retry_url, push_history=False, force=True)
            return
        if entry.is_dir:
            self.navigate_to(entry.url, push_history=True)
        elif entry.is_video:
            self._open_player_with_resume(
                entry.url, entry.name,
                subtitle_urls=subtitle_urls_for(entry, self.current_entries),
            )
        else:
            QMessageBox.information(
                self, "Archivo no reproducible",
                f"'{entry.name}' no parece un video soportado.\n\n"
                "Podés descargarlo con clic derecho › Descargar.",
            )

    def go_back(self):
        if self.history_pos > 0:
            self.history_pos -= 1
            self.navigate_to(self.history[self.history_pos], push_history=False)

    def go_forward(self):
        if self.history_pos < len(self.history) - 1:
            self.history_pos += 1
            self.navigate_to(self.history[self.history_pos], push_history=False)

    def refresh_current(self):
        if self.current_url:
            self.navigate_to(self.current_url, push_history=False, force=True)

    def _update_breadcrumb(self, url: str):
        path = urlparse(url).path
        parts = [_clean_title(p) for p in path.split("/") if p]
        crumb = "Inicio" + ("".join(f"  ›  {p}" for p in parts))
        self.breadcrumb.setText(crumb)

    # ------------------------------------------------------------------
    # Menú contextual (clic derecho) sobre archivos/carpetas
    # ------------------------------------------------------------------
    def _show_file_menu(self, pos):
        item = self.file_list.itemAt(pos)
        if not item:
            return
        entry: Entry = item.data(Qt.UserRole)
        menu = QMenu(self)

        if entry.is_dir:
            menu.addAction("Abrir carpeta", lambda: self.navigate_to(entry.url, push_history=True))
            menu.addAction("⬇ Descargar toda esta carpeta", lambda: self._download_folder_requested(entry))
        elif entry.is_video:
            menu.addAction(
                "▶ Reproducir",
                lambda: self._open_player_with_resume(
                    entry.url, entry.name,
                    subtitle_urls=subtitle_urls_for(entry, self.current_entries),
                ),
            )
            menu.addAction("⬇ Descargar", lambda: self._start_download(entry))
            menu.addSeparator()
            menu.addAction(
                "📺 Enviar a MiBox…",
                lambda: self._send_to_chromecast(
                    entry.url, entry.name,
                    subtitle_urls_for(entry, self.current_entries),
                ),
            )
            menu.addAction("⚙ Configurar MiBox…", self._configure_mibox)
            if self.chromecast.is_active():
                menu.addAction("⏹ Detener transmisión MiBox", self._stop_chromecast)
            if self.progress.fraction(entry.url) is not None:
                menu.addAction("Olvidar progreso", lambda: self._forget_progress(entry.url))
        else:
            menu.addAction("⬇ Descargar", lambda: self._start_download(entry))

        menu.exec(self.file_list.viewport().mapToGlobal(pos))

    def _forget_progress(self, url: str):
        self.progress.remove(url)
        self._populate_list(self.current_entries)

    # ------------------------------------------------------------------
    # Chromecast / MiBox (enviar a TV)
    # ------------------------------------------------------------------
    def _configure_mibox(self):
        current_ip = self.config.get("mibox_ip", "") or ""
        ip, ok = QInputDialog.getText(
            self, "Configurar MiBox",
            "IP del MiBox en tu red local (se ve en Ajustes › Red del propio\\n"
            "MiBox, o en la lista de dispositivos de tu router):",
            text=current_ip,
        )
        if not ok:
            return
        ip = ip.strip()
        # Validación estricta de IPv4; evita aceptar direcciones imposibles.
        import ipaddress
        try:
            ipaddress.IPv4Address(ip)
        except ipaddress.AddressValueError:
            QMessageBox.warning(self, "MiBox", f"'{ip}' no parece una IP válida.")
            return
        self.config.set("mibox_ip", ip)
        name, ok = QInputDialog.getText(
            self, "Configurar MiBox",
            "Nombre del dispositivo (como aparece al hacer cast; es opcional):",
            text=self.config.get("mibox_name", "MiBox") or "MiBox",
        )
        if ok and name.strip():
            self.config.set("mibox_name", name.strip())
        QMessageBox.information(self, "MiBox", f"MiBox configurado en {ip}.")

    def _send_to_chromecast(self, url: str, title: str, subtitle_urls: list | None = None):
        if not CHROMECAST_AVAILABLE:
            QMessageBox.information(self, "Enviar a MiBox", INSTALL_HINT)
            return
        ip = self.config.get("mibox_ip")
        if not ip:
            self._configure_mibox()
            ip = self.config.get("mibox_ip")
            if not ip:
                return
        name = self.config.get("mibox_name", "MiBox") or "MiBox"
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._io_executor.submit(
                self._chromecast_send_bg, url, title, subtitle_urls or [], ip, name
            )
        except RuntimeError:
            QGuiApplication.restoreOverrideCursor()

    def _chromecast_send_bg(self, url, title, subtitle_urls, ip, name):
        ok, msg = self.chromecast.send(
            url, title=title, subtitle_urls=subtitle_urls, host=ip, friendly_name=name
        )
        self._chromecast_ready_signal.emit(ok, msg)

    def _stop_chromecast(self):
        try:
            self._io_executor.submit(self._chromecast_stop_bg)
        except RuntimeError:
            pass

    def _chromecast_stop_bg(self):
        ok, msg = self.chromecast.stop()
        self._chromecast_ready_signal.emit(ok, msg)

    def _on_chromecast_ready(self, ok: bool, msg: str):
        QGuiApplication.restoreOverrideCursor()
        box = QMessageBox.information if ok else QMessageBox.warning
        box(self, "MiBox", msg)

    # ------------------------------------------------------------------
    # Reproducción
    # ------------------------------------------------------------------
    def _open_player_with_resume(self, url: str, title: str, subtitle_urls: list | None = None):
        resume_at = self.progress.resumable_position(url)
        start_at = 0.0

        if resume_at:
            box = QMessageBox(self)
            box.setWindowTitle("Continuar viendo")
            box.setText(
                f"Quedaste en {_fmt_time(resume_at)} de '{title}'.\n"
                "¿Querés continuar desde ahí o empezar de nuevo?"
            )
            continue_btn = box.addButton("Continuar", QMessageBox.AcceptRole)
            box.addButton("Empezar de nuevo", QMessageBox.RejectRole)
            box.setDefaultButton(continue_btn)
            box.exec()
            if box.clickedButton() is continue_btn:
                start_at = resume_at
            else:
                self.progress.remove(url)

        self._open_player(url, title, start_at=start_at, subtitle_urls=subtitle_urls)

    def _open_player(self, url: str, title: str, start_at: float = 0.0, subtitle_urls: list | None = None):
        self.stack.setCurrentWidget(self.player)
        self.close_player_btn.show()
        self.player.play(url, title=title, start_at=start_at, subtitle_urls=subtitle_urls)

    def _on_video_finished(self):
        """Al llegar al final de un video, si hay otro video después en
        la misma carpeta (orden alfabético del listado), lo reproduce
        automáticamente — pensado para series (episodio 1 → 2 → 3…)."""
        current_url = self.player.current_url
        if not current_url or not self.current_entries:
            return
        video_entries = [e for e in self.current_entries if e.is_video]
        urls = [e.url for e in video_entries]
        if current_url not in urls:
            return
        idx = urls.index(current_url)
        if idx + 1 >= len(video_entries):
            return  # era el último video de la carpeta, no hay más
        next_entry = video_entries[idx + 1]
        subs = subtitle_urls_for(next_entry, self.current_entries)
        self._open_player(next_entry.url, next_entry.name, subtitle_urls=subs)

    def _close_player(self):
        self._save_current_progress()
        self.player.stop()
        self.stack.setCurrentWidget(self.file_list)
        self.close_player_btn.hide()
        # refresca los porcentajes en la lista al volver
        self._populate_list(self.current_entries)

    def _save_current_progress(self):
        url = getattr(self.player, "current_url", None)
        if not url:
            return
        pos, dur = self.player.current_progress()
        if pos and dur:
            self.progress.set(url, pos, dur, title=self.player._current_title)

    def _show_error(self, message: str):
        QMessageBox.warning(self, "Reproductor", message)

    # ------------------------------------------------------------------
    # Descargas
    # ------------------------------------------------------------------
    def _start_download(self, entry: Entry, relative_dir: str = ""):
        task_id = self.downloads.enqueue(entry.url, entry.name, relative_dir=relative_dir)
        label = f"{relative_dir}/{entry.name}" if relative_dir else entry.name
        item = QListWidgetItem(f"⬇ {label}  —  en cola")
        item.setData(Qt.UserRole, task_id)
        self.downloads_list.addItem(item)
        self._download_items[task_id] = item

    # ------------------------------------------------------------------
    # Descargar una carpeta completa (recursivo, con subcarpetas)
    # ------------------------------------------------------------------
    def _download_folder_requested(self, entry: Entry):
        self.status_label.setText(f"Escaneando \"{entry.name}\"…")
        try:
            self._io_executor.submit(self._scan_folder_bg, entry.url, entry.name)
        except RuntimeError:
            pass

    def _scan_folder_bg(self, folder_url: str, root_name: str):
        files = []  # (url, filename, relative_dir)
        total_bytes = 0.0
        self._walk_folder_for_download(folder_url, root_name, files, depth=0)
        for _url, _filename, _relative_dir, size_str in files:
            total_bytes += _parse_size_to_bytes(size_str)
        # descartamos el tamaño del tuple para la señal (solo lo usamos acá)
        plain_files = [(u, f, r) for u, f, r, _s in files]
        self._folder_scan_ready_signal.emit(plain_files, root_name, total_bytes)

    def _walk_folder_for_download(self, folder_url: str, relative_dir: str, out: list, depth: int):
        if depth > 10 or len(out) >= 5000:  # topes de seguridad
            return
        listing = self.client.list_dir(folder_url, force=False)
        if listing.error:
            return
        for e in listing.entries:
            if e.is_dir:
                self._walk_folder_for_download(e.url, f"{relative_dir}/{e.name}", out, depth + 1)
            else:
                out.append((e.url, e.name, relative_dir, e.size))

    def _on_folder_scan_ready(self, files: list, root_name: str, total_bytes: float):
        if not files:
            self.status_label.setText(f"\"{root_name}\" no tiene archivos para descargar.")
            return

        size_label = _fmt_bytes(total_bytes) if total_bytes else "tamaño desconocido"
        box = QMessageBox(self)
        box.setWindowTitle("Descargar carpeta completa")
        box.setText(
            f"Se encontraron {len(files)} archivo(s) dentro de \"{root_name}\" "
            f"(~{size_label} en total, estimado).\n\n"
            f"Se van a guardar en:\n~/Downloads/VisualesUCLV/{root_name}/...\n\n"
            "¿Descargar todo?"
        )
        yes_btn = box.addButton("Descargar todo", QMessageBox.AcceptRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.setDefaultButton(yes_btn)
        box.exec()
        if box.clickedButton() is not yes_btn:
            self.status_label.setText("Descarga de carpeta cancelada.")
            return

        self.status_label.setText(f"Encolando {len(files)} archivo(s) de \"{root_name}\"…")
        for url, filename, relative_dir in files:
            fake_entry = Entry(name=filename, url=url, is_dir=False)
            self._start_download(fake_entry, relative_dir=relative_dir)

    def _on_download_progress(self, task_id: str, done: int, total: int):
        item = self._download_items.get(task_id)
        if not item:
            return
        task = self.downloads.tasks.get(task_id)
        name = task.filename if task else "archivo"
        if total:
            pct = int(done / total * 100)
            mb_done, mb_total = done / 1_048_576, total / 1_048_576
            item.setText(f"⬇ {name}\n   {pct}%  ({mb_done:.0f} / {mb_total:.0f} MB)")
        else:
            item.setText(f"⬇ {name}\n   {done / 1_048_576:.0f} MB descargados")

    def _on_download_status(self, task_id: str, status: str):
        item = self._download_items.get(task_id)
        if not item:
            return
        task = self.downloads.tasks.get(task_id)
        name = task.filename if task else "archivo"
        if status == "queued":
            item.setText(f"⬇ {name}\n   en cola")
        elif status == "downloading":
            item.setText(f"⬇ {name}\n   descargando…")
        elif status == "completed":
            item.setText(f"✅ {name}\n   completado")
        elif status == "canceled":
            item.setText(f"⛔ {name}\n   cancelado")
        elif status.startswith("error"):
            item.setText(f"⚠️ {name}\n   error al descargar")

    def _show_downloads_menu(self, pos):
        item = self.downloads_list.itemAt(pos)
        if not item:
            return
        task_id = item.data(Qt.UserRole)
        task = self.downloads.tasks.get(task_id)
        if not task:
            return
        menu = QMenu(self)
        if task.status in ("queued", "downloading"):
            menu.addAction("Cancelar", lambda: self.downloads.cancel(task_id))
        if task.status == "completed":
            menu.addAction("Mostrar en Finder", lambda: self._reveal_in_finder(task.dest_path))
        menu.exec(self.downloads_list.viewport().mapToGlobal(pos))

    def _reveal_in_finder(self, path: Path):
        import subprocess
        subprocess.run(["open", "-R", str(path)])

    def _open_downloads_folder(self):
        import subprocess
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(DOWNLOADS_DIR)])

    # ------------------------------------------------------------------
    # Favoritos
    # ------------------------------------------------------------------
    def _load_favorites(self) -> list[dict]:
        if FAVORITES_FILE.exists():
            try:
                return json.loads(FAVORITES_FILE.read_text())
            except Exception:
                return []
        return []

    def _save_favorites(self):
        FAVORITES_FILE.write_text(json.dumps(self.favorites, ensure_ascii=False, indent=2))

    def _refresh_fav_list(self):
        self.fav_list.clear()
        for fav in self.favorites:
            # unquote acá también, por si el favorito se guardó con el
            # bug viejo (nombre sin decodificar) antes de este arreglo
            item = QListWidgetItem(f"★ {_clean_title(fav['name'])}")
            item.setData(Qt.UserRole, fav["url"])
            self.fav_list.addItem(item)

    def _toggle_current_favorite(self):
        url = self.current_url
        if not url:
            return
        existing = next((f for f in self.favorites if f["url"] == url), None)
        if existing:
            self.favorites.remove(existing)
        else:
            name = [p for p in urlparse(url).path.split("/") if p]
            label = _clean_title(name[-1]) if name else "Inicio"
            self.favorites.append({"name": label, "url": url})
        self._save_favorites()
        self._refresh_fav_list()
        self._update_fav_button()

    def _update_fav_button(self):
        is_fav = any(f["url"] == self.current_url for f in self.favorites)
        self.fav_btn.setText("★ Favorito" if is_fav else "☆ Favorito")

    def _open_favorite(self, item: QListWidgetItem):
        url = item.data(Qt.UserRole)
        self.navigate_to(url, push_history=True)

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self._save_current_progress()
        self.player.shutdown()
        self.music_bar.shutdown()
        self._io_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
