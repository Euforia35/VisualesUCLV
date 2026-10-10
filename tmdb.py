"""
tmdb.py
-------
Busca sinopsis (en español) y póster de una película en The Movie
Database (TMDb) vía su API REST gratuita.

Requiere una API Key gratuita e instantánea: registrate en
https://www.themoviedb.org/settings/api (elegí "Developer" — es gratis
para uso personal/no comercial).

A diferencia de OMDb (que solo devuelve el texto tal cual está en IMDb,
casi siempre en inglés), TMDb sí permite pedir la sinopsis en un idioma
específico (acá pedimos "es-ES") cuando existe esa traducción cargada
— que es el caso de la gran mayoría de películas conocidas.
"""

from __future__ import annotations

import requests

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


def fetch_synopsis(
    api_key: str,
    title: str,
    year: str | None = None,
    language: str = "es-ES",
    media_type: str = "movie",
    timeout: float = 10.0,
) -> tuple[str, str, str]:
    """Devuelve (sinopsis, url_poster, error).

    media_type: "movie" o "tv" (series) — usan endpoints y nombres de
    parámetro de año distintos en la API de TMDb.
    """
    if not api_key:
        return "", "", "Falta la API Key de TMDb."
    if not title.strip():
        return "", "", "Falta un título para buscar."
    if media_type not in ("movie", "tv"):
        media_type = "movie"

    year_param = "first_air_date_year" if media_type == "tv" else "year"
    params = {"api_key": api_key, "query": title, "language": language}
    if year:
        params[year_param] = year

    try:
        resp = requests.get(f"{API_BASE}/search/{media_type}", params=params, timeout=timeout)
        if resp.status_code == 401:
            return "", "", "API Key de TMDb inválida."
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            tipo = "serie" if media_type == "tv" else "película"
            return "", "", f'No se encontró "{title}" ({tipo}) en TMDb.'

        best = results[0]
        overview = (best.get("overview") or "").strip()
        poster_path = best.get("poster_path") or ""
        poster_url = f"{IMAGE_BASE}{poster_path}" if poster_path else ""

        if not overview and language != "en-US":
            # Esta ficha todavía no tiene sinopsis cargada en español —
            # probamos en inglés antes de rendirnos, mejor eso que nada.
            params["language"] = "en-US"
            resp2 = requests.get(f"{API_BASE}/search/{media_type}", params=params, timeout=timeout)
            resp2.raise_for_status()
            results2 = (resp2.json().get("results")) or []
            if results2:
                overview = (results2[0].get("overview") or "").strip()
                if overview:
                    overview += "\n\n(No había sinopsis en español para este título — esta está en inglés.)"

        if not overview:
            return "", poster_url, f'"{title}" está en TMDb pero no tiene sinopsis cargada.'
        return overview, poster_url, ""
    except requests.RequestException as exc:
        return "", "", f"No se pudo conectar con TMDb: {exc}"
    except Exception as exc:
        return "", "", f"Error al buscar en TMDb: {exc}"
