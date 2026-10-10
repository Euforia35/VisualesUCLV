"""
movie_detail.py
---------------
Vista de detalle para una carpeta que "parece" una película o episodio
individual: carátula, sinopsis (de sinopsis.txt) y botón(es) de
reproducir. Sin fondo panorámico — solo lo esencial.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea
)


class MovieDetailView(QWidget):
    """Emite play_requested(url, nombre) cuando el usuario elige reproducir,
    show_files_requested cuando quiere ver el listado crudo de archivos,
    e imdb_requested cuando pide buscar la sinopsis en IMDb."""

    play_requested = Signal(str, str)
    chromecast_requested = Signal(str, str)
    show_files_requested = Signal()
    imdb_requested = Signal()

    POSTER_SIZE = (220, 320)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
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
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(32, 32, 32, 32)
        content_layout.setSpacing(28)

        self.poster_label = QLabel()
        self.poster_label.setFixedSize(*self.POSTER_SIZE)
        self.poster_label.setScaledContents(True)
        self.poster_label.setStyleSheet("background-color: #1E2127; border-radius: 8px;")
        poster_col = QVBoxLayout()
        poster_col.addWidget(self.poster_label)
        poster_col.addStretch(1)
        content_layout.addLayout(poster_col)

        info_col = QVBoxLayout()
        info_col.setSpacing(10)

        self.title_label = QLabel("")
        self.title_label.setObjectName("detailTitle")
        self.title_label.setWordWrap(True)
        info_col.addWidget(self.title_label)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("detailMeta")
        info_col.addWidget(self.meta_label)

        self.synopsis_label = QLabel("")
        self.synopsis_label.setObjectName("detailSynopsis")
        self.synopsis_label.setWordWrap(True)
        info_col.addWidget(self.synopsis_label)

        self.imdb_btn = QPushButton("🎬 Buscar sinopsis en español (TMDb)")
        self.imdb_btn.setToolTip("Busca en TMDb en español y reemplaza la sinopsis de acá arriba")
        self.imdb_btn.clicked.connect(self.imdb_requested.emit)
        info_col.addWidget(self.imdb_btn, alignment=Qt.AlignLeft)

        info_col.addSpacing(6)
        self.play_buttons_layout = QVBoxLayout()
        self.play_buttons_layout.setSpacing(8)
        info_col.addLayout(self.play_buttons_layout)

        info_col.addSpacing(6)
        self.files_btn = QPushButton("📁 Ver todos los archivos de la carpeta")
        info_col.addWidget(self.files_btn, alignment=Qt.AlignLeft)
        self.files_btn.clicked.connect(self.show_files_requested.emit)

        info_col.addStretch(1)
        content_layout.addLayout(info_col, stretch=1)

    # ------------------------------------------------------------------
    def reset(self, title: str):
        self.title_label.setText(title)
        self.meta_label.setText("")
        self.synopsis_label.setText("Cargando sinopsis…")
        placeholder = QPixmap(*self.POSTER_SIZE)
        placeholder.fill(QColor("#1E2127"))
        self.poster_label.setPixmap(placeholder)
        self._clear_play_buttons()

    def _clear_play_buttons(self):
        while self.play_buttons_layout.count():
            item = self.play_buttons_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def set_meta(self, text: str):
        self.meta_label.setText(text)

    def set_synopsis(self, text: str):
        self.synopsis_label.setText(
            text.strip() if text and text.strip() else "Sin sinopsis disponible para este título."
        )

    def set_videos(self, videos: list[tuple[str, str]]):
        """videos: lista de (url, nombre_para_mostrar_en_el_botón)."""
        self._clear_play_buttons()
        if not videos:
            self.play_buttons_layout.addWidget(QLabel("No se encontró ningún video en esta carpeta."))
            return
        if len(videos) == 1:
            url, name = videos[0]
            btn = QPushButton("▶  Reproducir")
            btn.setObjectName("playHeroBtn")
            btn.clicked.connect(lambda checked=False, u=url, n=name: self.play_requested.emit(u, n))
            self.play_buttons_layout.addWidget(btn)
            cast_btn = QPushButton("📺  Enviar a MiBox")
            cast_btn.setToolTip("Transmitir este video al Xiaomi MiBox / Chromecast")
            cast_btn.clicked.connect(
                lambda checked=False, u=url, n=name: self.chromecast_requested.emit(u, n)
            )
            self.play_buttons_layout.addWidget(cast_btn)
        else:
            for url, name in videos:
                row = QHBoxLayout()
                btn = QPushButton(f"▶  {name}")
                btn.setObjectName("playHeroBtn")
                btn.clicked.connect(
                    lambda checked=False, u=url, n=name: self.play_requested.emit(u, n)
                )
                cast_btn = QPushButton("📺")
                cast_btn.setToolTip("Enviar este episodio al MiBox")
                cast_btn.clicked.connect(
                    lambda checked=False, u=url, n=name: self.chromecast_requested.emit(u, n)
                )
                row.addWidget(btn, stretch=1)
                row.addWidget(cast_btn)
                self.play_buttons_layout.addLayout(row)

    def set_poster_pixmap(self, pixmap: QPixmap):
        self.poster_label.setPixmap(pixmap)
