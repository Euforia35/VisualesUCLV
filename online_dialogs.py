"""
online_dialogs.py
------------------
- ApiKeyDialog: diálogo genérico para pedirle al usuario una API Key la
  primera vez que usa una función que la necesita (OMDb, OpenSubtitles).
- SubtitleSearchDialog: buscar subtítulos en OpenSubtitles y elegir cuál
  descargar y usar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox,
)

import opensubtitles


class ApiKeyDialog(QDialog):
    """Pide una API Key, con un link a dónde conseguirla gratis."""

    def __init__(self, service_name: str, signup_url: str, instructions: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"API Key de {service_name}")
        self.resize(440, 220)
        self.api_key = ""

        layout = QVBoxLayout(self)
        label = QLabel(instructions)
        label.setWordWrap(True)
        layout.addWidget(label)

        link_btn = QPushButton(f"🔗 Conseguir una API Key gratis de {service_name}")
        link_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(signup_url)))
        layout.addWidget(link_btn)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Pegá tu API Key acá…")
        layout.addWidget(self.key_input)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Guardar")
        save_btn.setObjectName("playHeroBtn")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_save(self):
        key = self.key_input.text().strip()
        if not key:
            QMessageBox.warning(self, "Falta la API Key", "Pegá una API Key antes de guardar.")
            return
        self.api_key = key
        self.accept()


class SubtitleSearchDialog(QDialog):
    """Busca subtítulos en OpenSubtitles y permite elegir uno para usar."""

    subtitle_chosen = Signal(str)  # ruta local del archivo descargado

    def __init__(self, api_key: str, default_query: str, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.setWindowTitle("Buscar subtítulos online")
        self.resize(520, 460)
        self._results: list[dict] = []

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.query_input = QLineEdit(default_query)
        self.search_btn = QPushButton("🔍 Buscar")
        self.search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self.query_input, stretch=1)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self._on_item_chosen)
        layout.addWidget(self.results_list, stretch=1)

        btn_row = QHBoxLayout()
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.reject)
        self.use_btn = QPushButton("⬇ Descargar y usar")
        self.use_btn.setObjectName("playHeroBtn")
        self.use_btn.clicked.connect(self._on_use_clicked)
        btn_row.addWidget(close_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.use_btn)
        layout.addLayout(btn_row)

        if default_query:
            self._do_search()

    def _do_search(self):
        query = self.query_input.text().strip()
        self.status_label.setText("Buscando…")
        self.results_list.clear()
        results, error = opensubtitles.search(self.api_key, query)
        self._results = results
        if error:
            self.status_label.setText(error)
            return
        self.status_label.setText(f"{len(results)} resultado(s)")
        for r in results:
            label = f"[{r['language']}]  {r['filename']}   ({r['download_count']} descargas)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, r)
            self.results_list.addItem(item)

    def _on_item_chosen(self, item: QListWidgetItem):
        self._download_and_emit(item.data(Qt.UserRole))

    def _on_use_clicked(self):
        item = self.results_list.currentItem()
        if not item:
            QMessageBox.information(self, "Elegí un subtítulo", "Seleccioná un resultado de la lista primero.")
            return
        self._download_and_emit(item.data(Qt.UserRole))

    def _download_and_emit(self, result: dict):
        self.status_label.setText("Descargando…")
        path, error = opensubtitles.download(self.api_key, result["file_id"])
        if error:
            self.status_label.setText(error)
            return
        self.subtitle_chosen.emit(path)
        self.accept()
