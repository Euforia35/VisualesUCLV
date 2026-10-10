"""
downloads.py
------------
Descarga archivos del servidor en segundo plano (streaming a disco,
sin bloquear la UI), con progreso y cancelación. Pensado para conexiones
lentas/costosas: por defecto solo 2 descargas simultáneas y chunks
grandes para no saturar la CPU con demasiados eventos.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

CHUNK_SIZE = 256 * 1024          # 256 KB por lectura
PROGRESS_EMIT_STEP = 1_000_000   # emitir progreso cada ~1 MB, no por chunk


@dataclass
class DownloadTask:
    id: str
    url: str
    filename: str
    dest_path: Path
    status: str = "queued"       # queued | downloading | paused | completed | canceled | error
    bytes_done: int = 0
    total_bytes: int = 0
    error_message: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)


class DownloadManager(QObject):
    progress = Signal(str, int, int)      # task_id, bytes_done, total_bytes
    status_changed = Signal(str, str)     # task_id, status ("downloading", "completed", "error", "canceled")

    def __init__(self, session, dest_dir: Path, max_concurrent: int = 2):
        super().__init__()
        self.session = session
        self.dest_dir = dest_dir
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self.tasks: dict[str, DownloadTask] = {}

    # ------------------------------------------------------------------
    def enqueue(self, url: str, filename: str, relative_dir: str = "") -> str:
        """relative_dir (opcional): subcarpetas a respetar dentro de la
        carpeta de descargas, ej. "Peliculas/Interestelar (2014)" — para
        "descargar carpeta completa" sin mezclar todo junto."""
        task_id = str(uuid4())
        dest_path = self._unique_path(filename, relative_dir)
        task = DownloadTask(id=task_id, url=url, filename=filename, dest_path=dest_path)
        self.tasks[task_id] = task
        self.status_changed.emit(task_id, "queued")
        self.executor.submit(self._run, task_id)
        return task_id

    def cancel(self, task_id: str):
        task = self.tasks.get(task_id)
        if task:
            task.cancel_event.set()

    def open_folder_path(self) -> Path:
        return self.dest_dir

    # ------------------------------------------------------------------
    def _unique_path(self, filename: str, relative_dir: str = "") -> Path:
        target_dir = self.dest_dir
        if relative_dir:
            target_dir = target_dir / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        candidate = target_dir / filename
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        i = 1
        while True:
            alt = target_dir / f"{stem} ({i}){suffix}"
            if not alt.exists():
                return alt
            i += 1

    def _run(self, task_id: str):
        task = self.tasks[task_id]
        part_path = task.dest_path.with_name(task.dest_path.name + ".part")
        self.status_changed.emit(task_id, "downloading")

        try:
            with self.session.get(task.url, stream=True, timeout=30) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                task.total_bytes = total
                done = 0
                last_emit = 0

                with open(part_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if task.cancel_event.is_set():
                            raise InterruptedError("cancelado por el usuario")
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        task.bytes_done = done
                        if done - last_emit >= PROGRESS_EMIT_STEP or done == total:
                            self.progress.emit(task_id, done, total)
                            last_emit = done

            part_path.rename(task.dest_path)
            task.status = "completed"
            self.status_changed.emit(task_id, "completed")

        except InterruptedError:
            task.status = "canceled"
            part_path.unlink(missing_ok=True)
            self.status_changed.emit(task_id, "canceled")

        except Exception as exc:
            task.status = "error"
            task.error_message = str(exc)
            self.status_changed.emit(task_id, f"error:{exc}")
