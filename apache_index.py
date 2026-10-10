"""
apache_index.py
----------------
Cliente + parser para servidores Apache con "mod_autoindex" (listados
"Index of /..."), como visuales.uclv.cu.

Soporta tanto el formato clásico basado en <pre> como el "FancyIndexing"
basado en <table>. Es tolerante a variaciones porque solo confía en:
  - las etiquetas <a href="..."> dentro del listado
  - si el href termina en "/" -> es carpeta
  - el texto/columnas cercanas para tamaño y fecha (best-effort)
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# Extensiones que consideramos "reproducibles" con mpv.
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".3gp",
}
SUBTITLE_EXTS = {".srt", ".sub", ".ass", ".ssa", ".vtt"}
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".aac", ".wma", ".opus"}

IGNORED_NAMES = {"parent directory", "..", "."}

# Un user-agent de navegador normal evita bloqueos a nivel de WAF/proxy
# (esto es independiente del robots.txt del sitio, que solo aplica a
# crawlers automatizados de terceros, no a un cliente que el propio
# usuario ejecuta para navegar su propio contenido).
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Safari/605.1.15"
    )
}


@dataclass
class Entry:
    name: str
    url: str
    is_dir: bool
    size: str = ""
    date: str = ""

    @property
    def ext(self) -> str:
        if self.is_dir:
            return ""
        m = re.search(r"(\.[A-Za-z0-9]+)$", self.name)
        return m.group(1).lower() if m else ""

    @property
    def is_video(self) -> bool:
        return self.ext in VIDEO_EXTS

    @property
    def is_subtitle(self) -> bool:
        return self.ext in SUBTITLE_EXTS

    @property
    def is_audio(self) -> bool:
        return self.ext in AUDIO_EXTS


@dataclass
class DirListing:
    url: str
    entries: list = field(default_factory=list)
    fetched_at: float = 0.0
    error: str = ""


class ApacheIndexClient:
    """Descarga y parsea listados de directorio, con caché en memoria."""

    def __init__(
        self,
        base_url: str,
        cache_ttl: float = 120.0,
        connect_timeout: float = 15.0,
        read_timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.cache_ttl = cache_ttl
        # (connect_timeout, read_timeout): en redes con mucha latencia el
        # servidor puede tardar bastante en empezar a responder, así que el
        # read_timeout es generoso. Se puede ajustar acá si hace falta más.
        self.timeout = (connect_timeout, read_timeout)
        self._cache: dict[str, DirListing] = {}
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        # Reintenta automáticamente ante cortes/errores transitorios
        # (típico en conexiones inestables), con espera creciente entre
        # intentos, antes de darle el error final al usuario.
        retry = Retry(
            total=3,
            connect=3,
            read=2,
            backoff_factor=1.5,
            status_forcelist=(500, 502, 503, 504),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ------------------------------------------------------------------
    def list_dir(self, url: str | None = None, force: bool = False) -> DirListing:
        """Devuelve el listado de una carpeta (usa caché salvo force=True)."""
        target = url or self.base_url
        if not target.endswith("/"):
            target += "/"

        with self._lock:
            cached = self._cache.get(target)
            if cached and not force and (time.time() - cached.fetched_at) < self.cache_ttl:
                return cached

        listing = self._fetch(target)

        with self._lock:
            self._cache[target] = listing
        return listing

    def invalidate(self, url: str | None = None):
        with self._lock:
            if url is None:
                self._cache.clear()
            else:
                self._cache.pop(url, None)

    # ------------------------------------------------------------------
    def _fetch(self, url: str) -> DirListing:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            return DirListing(url=url, entries=[], fetched_at=time.time(), error=str(exc))

        entries = self._parse_html(resp.text, url)
        return DirListing(url=url, entries=entries, fetched_at=time.time())

    @staticmethod
    def _parse_html(html: str, base_url: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        results: dict[str, Entry] = {}

        for a in soup.find_all("a", href=True):
            href = a["href"]

            # Ignorar anclas, enlaces externos, enlaces a orden de columnas (?C=N;O=A)
            if href.startswith("#") or href.startswith("?"):
                continue
            if href.startswith("http://") or href.startswith("https://"):
                # Solo seguimos si apunta dentro del mismo host/carpeta base
                if not href.startswith(base_url):
                    continue

            full_url = urljoin(base_url, href)
            # Debe quedarse dentro de la carpeta actual (evita subir a index raíz, etc.)
            if not full_url.startswith(base_url):
                continue
            if full_url == base_url:
                continue

            name = unquote(href.rstrip("/").rsplit("/", 1)[-1])
            if not name or name.lower() in IGNORED_NAMES:
                continue

            is_dir = href.endswith("/")

            # best-effort: tamaño/fecha suelen estar en el texto que sigue
            # al <a> dentro de la misma fila (<tr>) o línea de <pre>.
            size, date = ApacheIndexClient._guess_size_date(a)

            entry = Entry(name=name, url=full_url, is_dir=is_dir, size=size, date=date)
            # Deduplicar (algunos temas repiten el link del ícono + el del nombre)
            results[full_url] = entry

        # Carpetas primero, luego archivos, orden alfabético case-insensitive
        return sorted(
            results.values(),
            key=lambda e: (not e.is_dir, e.name.lower()),
        )

    @staticmethod
    def _guess_size_date(anchor) -> tuple[str, str]:
        # Caso "FancyIndexing" con <table>: columnas siguientes en el <tr>
        tr = anchor.find_parent("tr")
        if tr:
            cells = [c.get_text(strip=True) for c in tr.find_all("td")]
            date_val, size_val = "", ""
            for cell in cells:
                if re.match(r"\d{4}-\d{2}-\d{2}", cell) or re.match(r"\d{2}-\w{3}-\d{4}", cell):
                    date_val = cell
                elif re.match(r"^\d+(\.\d+)?\s*[KMG]?$", cell, re.I) or cell.upper() in ("-",):
                    if cell != "-":
                        size_val = cell
            return size_val, date_val

        # Caso <pre>: el texto después del </a> en la misma línea
        tail = anchor.next_sibling
        text = ""
        while tail is not None and getattr(tail, "name", None) != "a":
            text += str(tail) if isinstance(tail, str) else tail.get_text()
            tail = tail.next_sibling
            if text.count("\n") > 0:
                break
        m = re.search(
            r"(\d{2}-\w{3}-\d{4}\s+\d{2}:\d{2})\s+([\d.]+[KMG]?|-)", text
        )
        if m:
            return m.group(2), m.group(1)
        return "", ""


def subtitle_urls_for(entry: Entry, siblings: list) -> list:
    """URLs de subtítulos hermanos que probablemente correspondan a este video.

    Empareja por nombre base: "peli.mp4" + "peli.srt", o con sufijo de
    idioma como "peli.es.srt" / "peli.spa.srt" (cualquier cosa que
    empiece con el mismo nombre base del video, antes de la extensión).
    """
    if entry.is_dir:
        return []
    base = re.sub(r"\.[A-Za-z0-9]+$", "", entry.name).lower()
    matches = []
    for sib in siblings:
        if sib.is_dir or not sib.is_subtitle or sib.url == entry.url:
            continue
        sib_base = re.sub(r"\.[A-Za-z0-9]+$", "", sib.name).lower()
        if sib_base == base or sib_base.startswith(base + "."):
            matches.append(sib.url)
    return matches


def is_probably_directory_listing(html: str) -> bool:
    """Heurística rápida para saber si una página es un listado Apache."""
    head = html[:2000].lower()
    return "index of /" in head or "parentdirectory" in head or "parent directory" in head


def friendly_error(raw_error: str) -> str:
    """Traduce errores técnicos de red a un mensaje entendible en español."""
    low = raw_error.lower()
    if "read timed out" in low or "timeout" in low:
        return (
            "El servidor tardó demasiado en responder (conexión lenta o "
            "congestionada). Probá de nuevo con el botón ⟳ — a veces el "
            "segundo intento sí carga."
        )
    if "connection refused" in low or "newconnectionerror" in low or "failed to establish" in low:
        return (
            "No se pudo conectar con visuales.uclv.cu. Revisá tu conexión "
            "a internet (o si necesitás estar dentro de cierta red/VPN "
            "para acceder al servidor)."
        )
    if "ssl" in low or "certificate" in low:
        return f"Problema de conexión segura (SSL) con el servidor: {raw_error}"
    if "name or service not known" in low or "nodename nor servname" in low:
        return "No se pudo resolver visuales.uclv.cu — revisá tu conexión a internet."
    return f"No se pudo cargar: {raw_error}"
