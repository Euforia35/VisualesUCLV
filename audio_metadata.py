"""
audio_metadata.py
------------------
Lee metadatos (título, artista, álbum, número de pista, carátula
embebida) de archivos de audio remotos, pidiendo solo un pedazo del
archivo por HTTP Range en vez de descargarlo entero — los tags ID3v2 /
Vorbis comment / MP4 atoms viven casi siempre al principio del archivo,
junto con la carátula si está embebida.

La duración exacta NO se calcula acá (requeriría el archivo completo
para la mayoría de los formatos) — se toma de mpv una vez que el track
se reproduce de verdad, que es gratis y exacto.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from thumbnails import _looks_like_real_image, _revalidate_and_reencode

try:
    from mutagen import File as MutagenFile
except Exception:  # por si mutagen no está instalado, degradamos con gracia
    MutagenFile = None

# Suficiente para cubrir ID3v2/Vorbis comments + carátula embebida en la
# gran mayoría de archivos reales. Si la carátula es muy pesada (>1MB,
# poco común), simplemente no se encuentra y se cae al respaldo de
# imagen suelta en la carpeta (ver thumbnails.py).
HEAD_BYTES = 900 * 1024


@dataclass
class Track:
    entry: object  # apache_index.Entry — evitamos import circular con solo el tipo
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    track_number: str | None = None
    cover_bytes: bytes | None = None
    loaded: bool = False
    error: str = ""

    @property
    def display_title(self) -> str:
        return self.title or self.entry.name


def _first(easy_tags, keys: list[str]) -> str | None:
    if not easy_tags:
        return None
    for key in keys:
        val = easy_tags.get(key)
        if val:
            return str(val[0]) if isinstance(val, list) else str(val)
    return None


def _extract_cover(raw) -> bytes | None:
    if raw is None:
        return None
    tags = getattr(raw, "tags", None)

    # MP3 / ID3
    if tags is not None and hasattr(tags, "getall"):
        try:
            apics = tags.getall("APIC")
            if apics:
                return apics[0].data
        except Exception:
            pass

    # FLAC / OGG (mutagen expone .pictures)
    pictures = getattr(raw, "pictures", None)
    if pictures:
        try:
            return pictures[0].data
        except Exception:
            pass

    # MP4 / M4A
    if tags is not None:
        try:
            covr = tags.get("covr")
            if covr:
                return bytes(covr[0])
        except Exception:
            pass

    return None


class AudioMetadataManager(QObject):
    """Descarga y parsea metadatos de audio en hilos de fondo."""

    metadata_ready = Signal(str, object)  # (url, Track)

    def __init__(self, session, max_workers: int = 3):
        super().__init__()
        self.session = session
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._cache: dict[str, Track] = {}
        self._inflight: set[str] = set()

    def request(self, entry):
        url = entry.url
        cached = self._cache.get(url)
        if cached:
            self.metadata_ready.emit(url, cached)
            return
        if url in self._inflight:
            return
        self._inflight.add(url)
        self.executor.submit(self._fetch, entry)

    def get_cached(self, url: str) -> Track | None:
        return self._cache.get(url)

    def _fetch(self, entry):
        track = Track(entry=entry)
        try:
            if MutagenFile is None:
                raise RuntimeError("mutagen no está instalado")
            resp = self.session.get(
                entry.url, headers={"Range": f"bytes=0-{HEAD_BYTES - 1}"}, timeout=15
            )
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.content

            easy = MutagenFile(io.BytesIO(data), easy=True)
            if easy and easy.tags:
                track.title = _first(easy.tags, ["title"])
                track.artist = _first(easy.tags, ["artist"])
                track.album = _first(easy.tags, ["album"])
                track.track_number = _first(easy.tags, ["tracknumber"])

            raw = MutagenFile(io.BytesIO(data))
            cover = _extract_cover(raw)
            # Misma cautela que con las carátulas de películas: una
            # carátula embebida corrupta puede crashear feo el
            # decodificador nativo de imágenes de macOS. La validamos y
            # regeneramos limpia con Pillow antes de aceptarla.
            if cover and _looks_like_real_image(cover):
                track.cover_bytes = _revalidate_and_reencode(cover)
        except Exception as exc:
            track.error = str(exc)
        finally:
            track.loaded = True
            self._cache[entry.url] = track
            self._inflight.discard(entry.url)
            self.metadata_ready.emit(entry.url, track)
