"""
opensubtitles.py
----------------
Búsqueda y descarga de subtítulos vía la API REST de OpenSubtitles.com.

Requiere una API Key gratuita: registrate como "Consumer" en
https://www.opensubtitles.com/en/consumers y creá una API Key ahí — es
gratis e instantáneo. Sin login, la cuenta gratuita tiene una cuota
chica de descargas por día (suele ser suficiente para uso casual).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests

API_BASE = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "VisualesUCLV v1.0"


def search(api_key: str, query: str, languages: str = "es,en", timeout: float = 15.0) -> tuple[list[dict], str]:
    """Devuelve (resultados, error). Cada resultado:
    {file_id, filename, language, download_count}."""
    if not api_key:
        return [], "Falta la API Key de OpenSubtitles."
    if not query.strip():
        return [], "Escribí algo para buscar."
    try:
        resp = requests.get(
            f"{API_BASE}/subtitles",
            params={"query": query, "languages": languages},
            headers={"Api-Key": api_key, "User-Agent": USER_AGENT},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return [], "API Key de OpenSubtitles inválida o vencida."
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {}) or {}
            files = attrs.get("files") or []
            if not files:
                continue
            results.append({
                "file_id": files[0].get("file_id"),
                "filename": files[0].get("file_name") or attrs.get("release") or "subtitulo",
                "language": attrs.get("language", "?"),
                "download_count": attrs.get("download_count", 0),
            })
        if not results:
            return [], f"No se encontraron subtítulos para \"{query}\"."
        return results, ""
    except requests.RequestException as exc:
        return [], f"No se pudo conectar con OpenSubtitles: {exc}"
    except Exception as exc:
        return [], f"Error al buscar subtítulos: {exc}"


def download(api_key: str, file_id: int, timeout: float = 20.0) -> tuple[str, str]:
    """Descarga el subtítulo y lo guarda en un archivo temporal local.
    Devuelve (ruta_local, error)."""
    if not api_key:
        return "", "Falta la API Key de OpenSubtitles."
    try:
        resp = requests.post(
            f"{API_BASE}/download",
            json={"file_id": file_id},
            headers={
                "Api-Key": api_key,
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        if resp.status_code == 406:
            return "", "Se agotó la cuota de descargas diaria de tu cuenta de OpenSubtitles."
        if resp.status_code == 401:
            return "", "API Key de OpenSubtitles inválida o vencida."
        resp.raise_for_status()
        data = resp.json()
        link = data.get("link")
        filename = data.get("file_name") or "subtitulo.srt"
        if not link:
            return "", "OpenSubtitles no devolvió un link de descarga."

        file_resp = requests.get(link, timeout=timeout)
        file_resp.raise_for_status()

        tmp_dir = Path(tempfile.gettempdir()) / "visualesuclv_subs"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # nombre único por si se descargan varios subtítulos en la sesión
        local_path = tmp_dir / f"{file_id}_{filename}"
        local_path.write_bytes(file_resp.content)
        return str(local_path), ""
    except requests.RequestException as exc:
        return "", f"No se pudo descargar el subtítulo: {exc}"
    except Exception as exc:
        return "", f"Error al descargar el subtítulo: {exc}"
