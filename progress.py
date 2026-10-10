"""
progress.py
-----------
Guarda el punto de reproducción de cada video (resume), indexado por
su URL, en un JSON local. También sirve para marcar "visto" cuando el
usuario llegó cerca del final.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# Umbrales para decidir si vale la pena ofrecer "continuar"
MIN_RESUME_SECONDS = 15          # si vio menos de esto, no ofrecemos resume
FINISHED_FRACTION = 0.95         # si superó esto, lo consideramos "visto"


class ProgressStore:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except Exception:
                self._data = {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def get(self, url: str) -> dict | None:
        return self._data.get(url)

    def set(self, url: str, position: float, duration: float, title: str = ""):
        if duration <= 0:
            return
        # Si terminó (o casi), borramos el progreso para no ofrecer resume
        # de algo ya visto por completo.
        if position / duration >= FINISHED_FRACTION:
            self._data.pop(url, None)
        else:
            self._data[url] = {
                "position": position,
                "duration": duration,
                "title": title,
                "updated_at": time.time(),
            }
        self._save()

    def remove(self, url: str):
        if url in self._data:
            self._data.pop(url, None)
            self._save()

    def fraction(self, url: str) -> float | None:
        """Devuelve la fracción vista (0-1) de un video, o None si no hay dato."""
        entry = self._data.get(url)
        if not entry or not entry.get("duration"):
            return None
        return entry["position"] / entry["duration"]

    def resumable_position(self, url: str) -> float | None:
        """Posición desde la que conviene ofrecer continuar, o None."""
        entry = self._data.get(url)
        if not entry:
            return None
        pos, dur = entry.get("position", 0), entry.get("duration", 0)
        if pos < MIN_RESUME_SECONDS or dur <= 0:
            return None
        if pos / dur >= FINISHED_FRACTION:
            return None
        return pos

    def list_in_progress(self) -> list[dict]:
        """Todo lo que tiene sentido ofrecer "continuar", más reciente
        primero. Cada item: {url, title, position, duration, fraction,
        updated_at}."""
        items = []
        for url, entry in self._data.items():
            pos, dur = entry.get("position", 0), entry.get("duration", 0)
            if dur <= 0 or pos < MIN_RESUME_SECONDS:
                continue
            frac = pos / dur
            if frac >= FINISHED_FRACTION:
                continue
            items.append({
                "url": url,
                "title": entry.get("title") or "",
                "position": pos,
                "duration": dur,
                "fraction": frac,
                "updated_at": entry.get("updated_at", 0),
            })
        items.sort(key=lambda it: it["updated_at"], reverse=True)
        return items
