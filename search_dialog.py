"""
search_dialog.py
-----------------
Diálogo de búsqueda global: busca en un índice cacheado de todo el
servidor (catalog.SiteCatalog) en vez de solo la carpeta actual.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem,
)


class GlobalSearchDialog(QDialog):
    """item_chosen(url, is_dir) al elegir un resultado.
    search_requested(query) cada vez que hay que (re)buscar — lo maneja
    main_window.py, que llama a show_results(...) con lo que encuentre.
    reindex_requested cuando el usuario pide reconstruir el índice."""

    item_chosen = Signal(str, bool)
    search_requested = Signal(str)
    reindex_requested = Signal()

    def __init__(self, initial_query: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Buscar en todo el servidor")
        self.resize(560, 480)
        self._pending_query = initial_query

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.query_input = QLineEdit(initial_query)
        self.query_input.setPlaceholderText("Título a buscar en todo el sitio…")
        self.query_input.textChanged.connect(self._request_search)
        self.query_input.returnPressed.connect(self._request_search)
        search_row.addWidget(self.query_input, stretch=1)
        layout.addLayout(search_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.results_list = QListWidget()
        self.results_list.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self.results_list, stretch=1)

        bottom_row = QHBoxLayout()
        self.reindex_btn = QPushButton("🔄 Reindexar todo el servidor")
        self.reindex_btn.setToolTip("Vuelve a recorrer todo el sitio desde cero (por si se agregó contenido nuevo)")
        self.reindex_btn.clicked.connect(self.reindex_requested.emit)
        bottom_row.addWidget(self.reindex_btn)
        bottom_row.addStretch(1)
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.reject)
        bottom_row.addWidget(close_btn)
        layout.addLayout(bottom_row)

    def _request_search(self):
        query = self.query_input.text().strip()
        self._pending_query = query
        self.search_requested.emit(query)

    def show_results(self, results: list, count_label: str):
        self.status_label.setText(count_label)
        self.results_list.clear()
        for r in results:
            icon = "📁" if r.is_dir else "🎬"
            item = QListWidgetItem(f"{icon}  {r.name}\n     {r.parent_path}")
            item.setData(Qt.UserRole, r)
            self.results_list.addItem(item)

    def set_indexing_status(self, count: int, current_path: str):
        short_path = current_path if len(current_path) < 60 else "…" + current_path[-57:]
        self.status_label.setText(f"⏳ Indexando… {count} elementos encontrados ({short_path})")
        self.reindex_btn.setEnabled(False)

    def set_index_ready(self, count: int):
        self.reindex_btn.setEnabled(True)
        self.status_label.setText(f"Índice listo: {count} elementos. Escribí para buscar.")
        if self._pending_query:
            self._request_search()

    def _on_item_activated(self, item: QListWidgetItem):
        r = item.data(Qt.UserRole)
        self.item_chosen.emit(r.url, r.is_dir)
        self.accept()
