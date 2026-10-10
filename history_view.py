"""
history_view.py
----------------
"Continuar viendo": lista de videos con reproducción en progreso
(series y películas), pensada como pantalla inicial de la app.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QPixmap, QColor, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
)

COVER_SIZE = (70, 100)


def _fmt_time(seconds: float) -> str:
    if seconds is None or seconds < 0:
        seconds = 0
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


class HistoryView(QWidget):
    """play_requested(url, title, position) al elegir continuar un
    ítem; remove_requested(url) al sacarlo del historial."""

    play_requested = Signal(str, str, float)
    remove_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 24)
        outer.setSpacing(14)

        title = QLabel("Continuar viendo")
        title.setObjectName("detailTitle")
        outer.addWidget(title)

        self.empty_label = QLabel(
            "Todavía no empezaste ninguna película o serie.\n"
            "Andá a 🏠 Inicio para explorar el catálogo."
        )
        self.empty_label.setObjectName("detailMeta")
        self.empty_label.setWordWrap(True)
        outer.addWidget(self.empty_label)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("fileList")
        self.list_widget.setIconSize(QSize(*COVER_SIZE))
        self.list_widget.setSpacing(4)
        self.list_widget.itemActivated.connect(self._on_item_activated)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        outer.addWidget(self.list_widget, stretch=1)

    # ------------------------------------------------------------------
    def load(self, items: list[dict]):
        self._items = items
        self.list_widget.clear()
        has_items = bool(items)
        self.empty_label.setVisible(not has_items)
        self.list_widget.setVisible(has_items)

        for item in items:
            pct = int(item["fraction"] * 100)
            remaining = item["duration"] - item["position"]
            text = (
                f"{item['title'] or '(sin título)'}\n"
                f"▶ {pct}% visto — quedan {_fmt_time(remaining)}"
            )
            litem = QListWidgetItem(text)
            placeholder = QPixmap(*COVER_SIZE)
            placeholder.fill(QColor("#1E2127"))
            litem.setIcon(QIcon(placeholder))
            litem.setData(Qt.UserRole, item)
            self.list_widget.addItem(litem)

    def set_cover(self, url: str, pixmap: QPixmap):
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.UserRole).get("url") == url:
                it.setIcon(QIcon(pixmap))
                break

    def remove_item(self, url: str):
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.UserRole).get("url") == url:
                self.list_widget.takeItem(i)
                break
        if self.list_widget.count() == 0:
            self.empty_label.setVisible(True)
            self.list_widget.setVisible(False)

    # ------------------------------------------------------------------
    def _on_item_activated(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole)
        self.play_requested.emit(data["url"], data.get("title") or "", data.get("position", 0.0))

    def _show_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        data = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction(
            "▶ Continuar",
            lambda: self.play_requested.emit(data["url"], data.get("title") or "", data.get("position", 0.0)),
        )
        menu.addAction("✕ Quitar del historial", lambda: self.remove_requested.emit(data["url"]))
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))
