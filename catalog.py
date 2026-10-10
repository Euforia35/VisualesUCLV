"""
catalog.py
----------
Construye y cachea un índice de TODO el árbol de carpetas del sitio
(recorriendo recursivamente desde la raíz), para poder buscar un
título sin tener que navegar carpeta por carpeta. Se guarda en disco y
se reutiliza entre sesiones — solo se vuelve a indexar cuando el
usuario lo pide o cuando el caché ya es viejo.

Corre en un hilo de fondo (ver main_window.py) — SiteCatalog es un
QObject con señales para reportar progreso, siguiendo el mismo patrón
seguro (hilos comunes + señales) que el resto de la app, no QThread.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from apache_index import ApacheIndexClient

DEFAULT_MAX_DEPTH = 6
STALE_AFTER_SECONDS = 3 * 24 * 3600  # 3 días
MAX_ENTRIES = 20000  # tope de seguridad ante un árbol enorme/loops raros


@dataclass
class CatalogEntry:
    name: str
    url: str
    is_dir: bool
    parent_path: str  # ruta legible tipo "Inicio › Peliculas › Accion"


class SiteCatalog(QObject):
    progress = Signal(int, str)   # (cantidad indexada hasta ahora, carpeta actual)
    finished = Signal(int)        # cantidad total indexada

    def __init__(self, client: ApacheIndexClient, cache_path: Path, max_depth: int = DEFAULT_MAX_DEPTH):
        super().__init__()
        self.client = client
        self.cache_path = cache_path
        self.max_depth = max_depth
        self.entries: list[CatalogEntry] = []
        self.built_at: float = 0.0
        self._cancel = False

    # ------------------------------------------------------------------
    def load_from_disk(self) -> bool:
        """Intenta cargar un catálogo cacheado (aunque esté viejo).
        Devuelve True si encontró y cargó algo."""
        if not self.cache_path.exists():
            return False
        try:
            data = json.loads(self.cache_path.read_text())
        except Exception:
            return False
        self.built_at = data.get("built_at", 0)
        self.entries = [
            CatalogEntry(name=e["name"], url=e["url"], is_dir=e["is_dir"], parent_path=e["parent_path"])
            for e in data.get("entries", [])
        ]
        return bool(self.entries)

    def is_stale(self) -> bool:
        return (time.time() - self.built_at) > STALE_AFTER_SECONDS

    def _save_to_disk(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "built_at": self.built_at,
                "entries": [
                    {"name": e.name, "url": e.url, "is_dir": e.is_dir, "parent_path": e.parent_path}
                    for e in self.entries
                ],
            }
            self.cache_path.write_text(json.dumps(data))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def build(self, root_url: str, root_label: str = "Inicio"):
        """Recorre el árbol completo. Pensado para correr en un hilo de
        fondo — emite `progress` de a poco y `finished` al terminar."""
        self.entries = []
        self._cancel = False
        self._walk(root_url, root_label, depth=0)
        self.built_at = time.time()
        self._save_to_disk()
        self.finished.emit(len(self.entries))

    def cancel(self):
        self._cancel = True

    def _walk(self, url: str, path_label: str, depth: int):
        if self._cancel or depth > self.max_depth or len(self.entries) >= MAX_ENTRIES:
            return
        listing = self.client.list_dir(url, force=False)
        if listing.error:
            return
        for entry in listing.entries:
            if self._cancel or len(self.entries) >= MAX_ENTRIES:
                return
            self.entries.append(CatalogEntry(
                name=entry.name, url=entry.url, is_dir=entry.is_dir, parent_path=path_label,
            ))
            if len(self.entries) % 20 == 0:
                self.progress.emit(len(self.entries), path_label)
            if entry.is_dir:
                self._walk(entry.url, f"{path_label} › {entry.name}", depth + 1)
        self.progress.emit(len(self.entries), path_label)

    # ------------------------------------------------------------------
    def search(self, query: str, limit: int = 100) -> list[CatalogEntry]:
        query = query.strip().lower()
        if not query:
            return []
        results = [e for e in self.entries if query in e.name.lower()]
        # carpetas primero (suele ser "la película/serie" en sí, más
        # relevante que un archivo suelto adentro), después archivos
        results.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return results[:limit]
