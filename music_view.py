"""
music_view.py
-------------
- MusicAlbumView: lista de tracks de una carpeta de música, con
  carátula y metadatos (título/artista) que se van completando a
  medida que llegan.
- MusicPlayerBar: barra de reproducción persistente (tipo Spotify) que
  se mantiene visible sin importar qué carpeta estés mirando. Maneja su
  propia cola de reproducción vía mpv (proceso aparte, como el video).
- LyricsDialog: ventanita con el resultado de la búsqueda de letra.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QSlider, QDialog, QTextEdit, QScrollArea,
)

from mpv_ipc import MpvIPCPlayer, find_mpv_binary

MUSIC_MPV_AVAILABLE = find_mpv_binary() is not None

ALBUM_COVER_SIZE = (200, 200)
BAR_COVER_SIZE = (48, 48)


def _fmt_time(seconds: float) -> str:
    if seconds is None or seconds < 0:
        seconds = 0
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    return f"{m:d}:{s:02d}"


class MusicAlbumView(QWidget):
    """play_requested(tracks, start_index, shuffle) — tracks es una lista
    de dicts {url, title, artist, cover}."""

    play_requested = Signal(list, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        self._meta: dict[str, object] = {}  # url -> Track
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll, stretch=1)

        content = QWidget()
        scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 32, 32, 24)
        content_layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(22)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(*ALBUM_COVER_SIZE)
        self.cover_label.setScaledContents(True)
        self.cover_label.setStyleSheet("background-color: #1E2127; border-radius: 8px;")
        header.addWidget(self.cover_label, alignment=Qt.AlignTop)

        info_col = QVBoxLayout()
        self.title_label = QLabel("")
        self.title_label.setObjectName("detailTitle")
        self.title_label.setWordWrap(True)
        info_col.addWidget(self.title_label)

        self.count_label = QLabel("")
        self.count_label.setObjectName("detailMeta")
        info_col.addWidget(self.count_label)

        btn_row = QHBoxLayout()
        self.play_all_btn = QPushButton("▶  Reproducir todo")
        self.play_all_btn.setObjectName("playHeroBtn")
        self.play_all_btn.clicked.connect(lambda: self._emit_play(0, shuffle=False))
        self.shuffle_btn = QPushButton("🔀  Aleatorio")
        self.shuffle_btn.clicked.connect(lambda: self._emit_play(0, shuffle=True))
        btn_row.addWidget(self.play_all_btn)
        btn_row.addWidget(self.shuffle_btn)
        btn_row.addStretch(1)
        info_col.addLayout(btn_row)
        info_col.addStretch(1)

        header.addLayout(info_col, stretch=1)
        content_layout.addLayout(header)

        self.track_list = QListWidget()
        self.track_list.setObjectName("fileList")
        self.track_list.itemActivated.connect(self._on_item_activated)
        content_layout.addWidget(self.track_list, stretch=1)

    # ------------------------------------------------------------------
    def load(self, entries: list, title: str):
        self._entries = [e for e in entries if e.is_audio]
        self._meta = {}
        self.title_label.setText(title)
        self.count_label.setText(f"{len(self._entries)} canción(es)")
        placeholder = QPixmap(*ALBUM_COVER_SIZE)
        placeholder.fill(QColor("#1E2127"))
        self.cover_label.setPixmap(placeholder)

        self.track_list.clear()
        for i, entry in enumerate(self._entries):
            item = QListWidgetItem(f"{i + 1}.  {entry.name}")
            item.setData(Qt.UserRole, entry)
            self.track_list.addItem(item)

    def update_track_meta(self, url: str, track):
        self._meta[url] = track
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            entry = item.data(Qt.UserRole)
            if entry.url != url:
                continue
            num = track.track_number or str(i + 1)
            num = str(num).split("/")[0]  # algunos tags traen "3/12"
            text = f"{num}.  {track.display_title}"
            if track.artist:
                text += f"   —   {track.artist}"
            item.setText(text)
            break

    def set_cover_pixmap(self, pixmap: QPixmap):
        self.cover_label.setPixmap(pixmap)

    # ------------------------------------------------------------------
    def _on_item_activated(self, item: QListWidgetItem):
        idx = self.track_list.row(item)
        self._emit_play(idx, shuffle=False)

    def _emit_play(self, start_index: int, shuffle: bool):
        if not self._entries:
            return
        tracks = []
        for entry in self._entries:
            meta = self._meta.get(entry.url)
            tracks.append({
                "url": entry.url,
                "title": (meta.display_title if meta else entry.name),
                "artist": (meta.artist if meta else "") or "",
                "cover": (meta.cover_bytes if meta else None),
            })
        self.play_requested.emit(tracks, start_index, shuffle)


class MusicPlayerBar(QWidget):
    """Barra de reproducción persistente, visible sin importar qué
    carpeta estés mirando (como la barra inferior de Spotify)."""

    lyrics_requested = Signal(str, str)  # (artist, title)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mpv: MpvIPCPlayer | None = None
        self._tracks: list[dict] = []
        self._current_index = -1
        self._seeking = False
        self._duration = 0.0
        self._build_ui()
        self.hide()  # oculta hasta que se reproduzca algo

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _build_ui(self):
        self.setObjectName("musicBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(14)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(*BAR_COVER_SIZE)
        self.cover_label.setScaledContents(True)
        self.cover_label.setStyleSheet("background-color: #1E2127; border-radius: 4px;")
        row.addWidget(self.cover_label)

        info_col = QVBoxLayout()
        info_col.setSpacing(0)
        self.title_label = QLabel("")
        self.title_label.setObjectName("nowPlayingLabel")
        self.artist_label = QLabel("")
        self.artist_label.setObjectName("detailMeta")
        info_col.addWidget(self.title_label)
        info_col.addWidget(self.artist_label)
        row.addLayout(info_col)
        row.setStretchFactor(info_col, 0)

        self.prev_btn = QPushButton("⏮")
        self.prev_btn.setFixedWidth(36)
        self.prev_btn.clicked.connect(self.prev)
        self.play_btn = QPushButton("⏸")
        self.play_btn.setFixedWidth(36)
        self.play_btn.clicked.connect(self.toggle_pause)
        self.next_btn = QPushButton("⏭")
        self.next_btn.setFixedWidth(36)
        self.next_btn.clicked.connect(self.next)
        row.addWidget(self.prev_btn)
        row.addWidget(self.play_btn)
        row.addWidget(self.next_btn)

        self.time_label = QLabel("0:00")
        self.time_label.setFixedWidth(38)
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_slider.sliderReleased.connect(self._on_seek_end)
        self.duration_label = QLabel("0:00")
        self.duration_label.setFixedWidth(38)
        row.addWidget(self.time_label)
        row.addWidget(self.seek_slider, stretch=1)
        row.addWidget(self.duration_label)

        self.shuffle_btn = QPushButton("🔀")
        self.shuffle_btn.setFixedWidth(34)
        self.shuffle_btn.setToolTip("Mezclar la cola")
        self.shuffle_btn.clicked.connect(self._on_shuffle_clicked)
        self.loop_btn = QPushButton("🔁")
        self.loop_btn.setFixedWidth(34)
        self.loop_btn.setCheckable(True)
        self.loop_btn.setToolTip("Repetir cola")
        self.loop_btn.clicked.connect(self._on_loop_toggled)
        self.lyrics_btn = QPushButton("📝")
        self.lyrics_btn.setFixedWidth(34)
        self.lyrics_btn.setToolTip("Ver letra")
        self.lyrics_btn.clicked.connect(self._on_lyrics_clicked)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        row.addWidget(self.shuffle_btn)
        row.addWidget(self.loop_btn)
        row.addWidget(self.lyrics_btn)
        row.addWidget(QLabel("🔊"))
        row.addWidget(self.volume_slider)

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(28)
        close_btn.setToolTip("Detener y ocultar")
        close_btn.clicked.connect(self.stop)
        row.addWidget(close_btn)

    # ------------------------------------------------------------------
    def play_queue(self, tracks: list[dict], start_index: int = 0, shuffle: bool = False):
        if not tracks:
            return
        if not MUSIC_MPV_AVAILABLE:
            self.title_label.setText("No se encontró 'mpv' — instalalo con: brew install mpv")
            self.show()
            return
        self._tracks = tracks
        self._current_index = max(0, min(start_index, len(tracks) - 1))
        if not self._mpv or not self._mpv.is_running():
            self._mpv = MpvIPCPlayer()
        urls = [t["url"] for t in tracks]
        self._mpv.load_playlist(
            urls, start_index=self._current_index,
            title=tracks[self._current_index]["title"], audio_only=True,
        )
        if shuffle:
            self._mpv.shuffle_playlist()
        self.play_btn.setText("⏸")
        self._update_now_playing()
        self.show()

    def _update_now_playing(self):
        if not (0 <= self._current_index < len(self._tracks)):
            return
        t = self._tracks[self._current_index]
        self.title_label.setText(t.get("title") or "")
        self.artist_label.setText(t.get("artist") or "")
        cover = t.get("cover")
        if cover:
            pix = QPixmap()
            if pix.loadFromData(cover):
                self.cover_label.setPixmap(pix)
                return
        placeholder = QPixmap(*BAR_COVER_SIZE)
        placeholder.fill(QColor("#1E2127"))
        self.cover_label.setPixmap(placeholder)

    # ------------------------------------------------------------------
    def toggle_pause(self):
        if not self._mpv or not self._mpv.is_running():
            return
        current = self._mpv.get_cached_property("pause", False)
        new_val = not current
        self._mpv.set_property("pause", new_val)
        self.play_btn.setText("▶" if new_val else "⏸")

    def next(self):
        if self._mpv and self._mpv.is_running():
            self._mpv.playlist_next()

    def prev(self):
        if self._mpv and self._mpv.is_running():
            self._mpv.playlist_prev()

    def _on_shuffle_clicked(self):
        if self._mpv and self._mpv.is_running():
            self._mpv.shuffle_playlist()

    def _on_loop_toggled(self):
        if self._mpv and self._mpv.is_running():
            self._mpv.set_loop_playlist(self.loop_btn.isChecked())

    def _on_volume_changed(self, value: int):
        if self._mpv and self._mpv.is_running():
            self._mpv.set_volume(value)

    def _on_lyrics_clicked(self):
        if 0 <= self._current_index < len(self._tracks):
            t = self._tracks[self._current_index]
            self.lyrics_requested.emit(t.get("artist") or "", t.get("title") or "")

    def _on_seek_end(self):
        if self._mpv and self._mpv.is_running() and self._duration:
            frac = self.seek_slider.value() / 1000.0
            self._mpv.seek(frac * self._duration, absolute=True)
        self._seeking = False

    def stop(self):
        if self._mpv:
            self._mpv.terminate()
            self._mpv = None
        self._tracks = []
        self._current_index = -1
        self.hide()

    def current_track(self) -> dict | None:
        if 0 <= self._current_index < len(self._tracks):
            return self._tracks[self._current_index]
        return None

    def shutdown(self):
        self._timer.stop()
        if self._mpv:
            self._mpv.terminate()
            self._mpv = None

    # ------------------------------------------------------------------
    def _refresh(self):
        if not self._mpv or not self._mpv.is_running():
            return

        pos_index = self._mpv.get_cached_property("playlist-pos", -1)
        try:
            pos_index = int(pos_index)
        except (TypeError, ValueError):
            pos_index = -1
        if pos_index >= 0 and pos_index != self._current_index and pos_index < len(self._tracks):
            self._current_index = pos_index
            self._update_now_playing()

        if self._seeking:
            return
        pos = self._mpv.get_cached_property("time-pos", 0.0) or 0.0
        dur = self._mpv.get_cached_property("duration", 0.0) or 0.0
        self._duration = dur
        self.time_label.setText(_fmt_time(pos))
        self.duration_label.setText(_fmt_time(dur))
        if dur:
            self.seek_slider.setValue(int((pos / dur) * 1000))


class LyricsDialog(QDialog):
    def __init__(self, artist: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Letra — {title}")
        self.resize(460, 580)
        layout = QVBoxLayout(self)

        header = QLabel(f"{title}\n{artist}")
        header.setObjectName("detailTitle")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setPlainText("Buscando letra…")
        layout.addWidget(self.text_area, stretch=1)

    def set_text(self, text: str):
        self.text_area.setPlainText(text)
