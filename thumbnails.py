"""
thumbnails.py
-------------
Busca y descarga (con caché en disco y memoria) carátulas/pósters para
carpetas y videos.

Heurística de búsqueda (el servidor no expone una API de metadatos,
así que probamos convenciones típicas de estructuras "listas para Kodi"):

  1. Un archivo hermano con el mismo nombre base y extensión de imagen
     en la MISMA carpeta (ej. "Breaking Bad/" + "Breaking Bad.jpg", o
     "episodio01.mp4" + "episodio01.jpg"). No cuesta una request extra
     de listado porque ya tenemos las entradas de la carpeta actual.
  2. Para carpetas, nombres típicos de Kodi DENTRO de la carpeta:
     poster.jpg, folder.jpg, cover.jpg (y variantes .png/.jpeg/.webp).
     Esto sí es una request HTTP directa (sin listar el directorio),
     que falla silenciosamente (404) si no existe.

Si estas convenciones no calzan con la estructura real del sitio, se
pueden ajustar fácilmente las listas POSTER_CANDIDATE_NAMES / IMAGE_EXTS
de este archivo.
"""

from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

from PySide6.QtCore import QObject, Signal
from PIL import Image

from apache_index import Entry

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp"]
POSTER_CANDIDATE_NAMES = ["poster", "folder", "cover"]


def _strip_ext(name: str) -> str:
    if "." in name:
        return name.rsplit(".", 1)[0]
    return name


def candidate_urls_for(entry: Entry, siblings: list[Entry]) -> list[str]:
    """Devuelve URLs candidatas a carátula para una entrada, en orden de prioridad."""
    candidates: list[str] = []
    base = _strip_ext(entry.name).lower()

    # 1) hermano con mismo nombre base + extensión de imagen
    for sib in siblings:
        if sib.is_dir or sib.url == entry.url:
            continue
        if _strip_ext(sib.name).lower() == base and sib.ext in IMAGE_EXTS:
            candidates.append(sib.url)

    # 2) para carpetas, nombres típicos de Kodi dentro de la carpeta
    if entry.is_dir:
        folder_url = entry.url if entry.url.endswith("/") else entry.url + "/"
        for name in POSTER_CANDIDATE_NAMES:
            for ext in IMAGE_EXTS:
                candidates.append(urljoin(folder_url, name + ext))

    return candidates


def in_folder_poster_candidates(entries: list[Entry]) -> list[str]:
    """Elige la mejor imagen para usar como póster DENTRO de una carpeta
    ya abierta (a diferencia de candidate_urls_for, que es para el caso
    de mostrar la carátula de una carpeta vista desde AFUERA)."""
    imgs = [e for e in entries if not e.is_dir and e.ext in IMAGE_EXTS]
    priority = [e for e in imgs if "poster" in e.name.lower()]
    priority += [e for e in imgs if "cover" in e.name.lower() and e not in priority]
    others = [e for e in imgs if e not in priority]
    return [e.url for e in (priority + others)]


def in_folder_backdrop_candidates(entries: list[Entry]) -> list[str]:
    """Imagen tipo fanart/backdrop (panorámica) dentro de la carpeta, si hay."""
    imgs = [e for e in entries if not e.is_dir and e.ext in IMAGE_EXTS]
    matches = [
        e for e in imgs
        if any(k in e.name.lower() for k in ("fanart", "backdrop", "background"))
    ]
    return [e.url for e in matches]


def _looks_like_real_image(data: bytes) -> bool:
    """Valida por los bytes reales del archivo (firma/"magic bytes"),
    no por lo que diga la extensión de la URL o el content-type del
    servidor. Esto es clave para las carátulas ADIVINADAS (poster.jpg,
    folder.jpg, etc.): si no existen de verdad, algunos servidores
    devuelven una página de error disfrazada de 200 OK, y pasarle esos
    bytes a QPixmap puede crashear feo en macOS (excepción nativa de
    Cocoa que ni siquiera Python puede atajar con try/except)."""
    if not data or len(data) < 12:
        return False
    if data.startswith(b"\xff\xd8\xff"):          # JPEG
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):      # PNG
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):    # GIF
        return True
    if data[:2] == b"BM":                          # BMP
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WEBP
        return True
    return False


def _revalidate_and_reencode(data: bytes) -> bytes | None:
    """Segunda barrera, más fuerte que _looks_like_real_image: decodifica
    la imagen completa con Pillow y la re-guarda limpia como PNG.

    ¿Por qué? _looks_like_real_image solo mira los primeros bytes (la
    "firma" del formato). Un archivo truncado a mitad de descarga, o
    corrupto de otra forma, puede tener una firma perfectamente válida
    y aun así crashear al decodificador nativo de imágenes de macOS con
    una excepción que ni Python puede atajar (aborta todo el proceso).
    Pillow, en cambio, lanza una excepción NORMAL de Python ante datos
    corruptos, así que la usamos como "cuarentena": si Pillow no puede
    abrir y decodificar la imagen entera sin errores, la descartamos
    acá y no llega nunca a Qt/macOS.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()  # detecta truncamientos/corrupción
        img = Image.open(io.BytesIO(data))  # verify() invalida el objeto; reabrimos
        img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None


class ThumbnailManager(QObject):
    """Descarga carátulas en hilos de trabajo y notifica a la UI vía señal.

    La decodificación a QPixmap se hace en el hilo principal (el slot
    conectado a `thumbnail_ready`), porque QPixmap no es seguro de crear
    fuera del hilo de la GUI. Acá solo viajan bytes crudos.
    """

    thumbnail_ready = Signal(str, bytes)   # key (normalmente entry.url), datos de imagen
    thumbnail_missing = Signal(str)        # key sin carátula encontrada

    def __init__(self, session, cache_dir: Path, max_workers: int = 4):
        super().__init__()
        self.session = session
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._inflight: set[str] = set()
        self._done: set[str] = set()

    def request(self, key: str, candidates: list[str]):
        if key in self._inflight or key in self._done:
            return
        if not candidates:
            self.thumbnail_missing.emit(key)
            return
        self._inflight.add(key)
        self.executor.submit(self._fetch, key, candidates)

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.img"

    def _fetch(self, key: str, candidates: list[str]):
        try:
            cache_path = self._cache_path(key)
            if cache_path.exists() and cache_path.stat().st_size > 0:
                cached = cache_path.read_bytes()
                if _looks_like_real_image(cached):
                    self.thumbnail_ready.emit(key, cached)
                    self._done.add(key)
                    return
                # caché corrupta de una versión anterior sin esta validación:
                # la descartamos y probamos de nuevo desde los candidatos
                try:
                    cache_path.unlink()
                except OSError:
                    pass

            for url in candidates:
                try:
                    resp = self.session.get(url, timeout=8)
                except Exception:
                    continue
                if resp.status_code != 200 or not resp.content:
                    continue
                if not _looks_like_real_image(resp.content):
                    continue  # no es una imagen real (probablemente 404 disfrazado)
                safe_data = _revalidate_and_reencode(resp.content)
                if safe_data is None:
                    continue  # firma válida pero imagen corrupta/truncada: probamos el siguiente candidato
                try:
                    cache_path.write_bytes(safe_data)
                except Exception:
                    pass
                self.thumbnail_ready.emit(key, safe_data)
                self._done.add(key)
                return

            self.thumbnail_missing.emit(key)
        finally:
            self._inflight.discard(key)
