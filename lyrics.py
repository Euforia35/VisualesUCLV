"""
lyrics.py
---------
Busca letras de canciones en lyrics.ovh, un servicio público gratuito
sin necesidad de API key. No es infalible: no tiene todo el catálogo
existente, y al ser un servicio de terceros gratuito podría dejar de
funcionar en cualquier momento — se maneja como best-effort, con
mensajes de error claros cuando no encuentra algo.
"""

from __future__ import annotations

from urllib.parse import quote

import requests


def fetch_lyrics(artist: str, title: str, timeout: float = 10.0) -> tuple[str, str]:
    """Devuelve (letra, error). Si error es "", la letra es válida."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return "", "Falta artista o título para poder buscar la letra."

    url = f"https://api.lyrics.ovh/v1/{quote(artist)}/{quote(title)}"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 404:
            return "", f"No se encontró letra para \"{title}\" de {artist}."
        resp.raise_for_status()
        data = resp.json()
        lyrics = (data.get("lyrics") or "").strip()
        if not lyrics:
            return "", f"No se encontró letra para \"{title}\" de {artist}."
        return lyrics, ""
    except requests.RequestException as exc:
        return "", f"No se pudo buscar la letra (sin conexión con el servicio): {exc}"
    except Exception as exc:
        return "", f"No se pudo buscar la letra: {exc}"
