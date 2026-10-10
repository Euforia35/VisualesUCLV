"""
mpv_ipc.py
----------
Controla mpv como un PROCESO APARTE (el binario real de `mpv`,
instalado por Homebrew), en vez de embeberlo en el mismo proceso vía
libmpv/python-mpv.

¿Por qué? En macOS, tanto Qt como el backend de video de mpv necesitan
manejar la cola de eventos de Cocoa (NSApplication) desde el hilo
principal. Cuando libmpv corre DENTRO del mismo proceso que Qt, los
dos compiten por esa cola y el resultado son crashes intermitentes
("QObject::setParent: ... different thread", segfaults) muy difíciles
de rastrear.

Lanzar mpv como un proceso separado —lo mismo que pasaría si escribieras
`mpv archivo.mp4` en la terminal— le da su propia cola de eventos de
Cocoa independiente, así que no hay conflicto posible. Lo controlamos
desde la app por el protocolo JSON IPC que mpv expone en un socket Unix
(`--input-ipc-server`), la misma técnica que usan la mayoría de las
interfaces gráficas de terceros para mpv (no es un hack: es la forma
soportada oficialmente de controlarlo desde afuera).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid


def find_mpv_binary() -> str | None:
    """Ubica el EJECUTABLE `mpv` (el programa, no la librería libmpv)."""
    found = shutil.which("mpv")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/mpv", "/usr/local/bin/mpv"):
        if os.path.exists(candidate):
            return candidate
    return None


class MpvIPCPlayer:
    """Lanza mpv como proceso aparte y lo controla por su socket IPC."""

    def __init__(self, mpv_path: str | None = None):
        self.mpv_path = mpv_path or find_mpv_binary()
        self.process: subprocess.Popen | None = None
        self.socket_path = os.path.join(
            tempfile.gettempdir(), f"visualesuclv-mpv-{uuid.uuid4().hex[:8]}.sock"
        )
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stop_reading = threading.Event()
        self._props_lock = threading.Lock()
        self._properties: dict[str, float] = {"time-pos": 0.0, "duration": 0.0}
        self._next_observe_id = 1
        self._observed: set[str] = set()
        self._callbacks: dict[str, list] = {}
        self._event_callbacks: dict[str, list] = {}
        self.last_error: str = ""
        self._stderr_log_path: str | None = None

    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, url: str, title: str = "", audio_only: bool = False, _retry_minimal: bool = False) -> bool:
        """Arranca mpv reproduciendo `url`. Devuelve False si no pudo.

        audio_only=True agrega --vid=no (sin pista de video) y muestra
        una ventanita chica en vez de una ventana grande vacía — útil
        para música, donde no necesitamos que se abra una ventana de
        video grande para un archivo que no tiene video.

        _retry_minimal=True es para uso interno: si el arranque normal
        (con los flags de caché/buffer) falla por lo que sea —typo,
        opción vieja/renombrada según la versión de mpv instalada,
        etc.—, reintentamos automáticamente UNA vez sin esos flags
        extra, para no dejar a alguien sin poder reproducir nada por
        un solo flag mal.
        """
        if not self.mpv_path:
            return False

        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass

        args = [
            self.mpv_path,
            f"--input-ipc-server={self.socket_path}",
            "--force-window=yes",
            "--idle=yes",
            "--keep-open=yes",
            f"--title={title or 'Visuales UCLV'}",
        ]
        if not _retry_minimal:
            # Caché/buffer para streaming remoto sobre conexiones lentas o
            # de latencia alta: sin esto mpv usa su caché por defecto, que
            # no está pensada para sesiones largas. Si alguno de estos
            # flags no es compatible con la versión de mpv instalada,
            # el bloque de abajo lo detecta y reintenta sin ellos —
            # mejor reproducir sin la optimización que no reproducir nada.
            args += [
                "--cache=yes",
                "--demuxer-readahead-secs=300",
                "--demuxer-max-bytes=150MiB",
                "--demuxer-max-back-bytes=75MiB",
                "--network-timeout=60",
            ]
        if audio_only:
            args += ["--vid=no", "--geometry=480x140", "--autofit=480x140"]
        args.append(url)

        self.last_error = ""
        # Volcamos stderr a un ARCHIVO, no a un PIPE: un pipe que nadie
        # lee continuamente se llena y hace que el proceso hijo se
        # quede colgado esperando poder escribir (deadlock clásico de
        # subprocess). Un archivo nunca bloquea al que escribe.
        self._stderr_log_path = os.path.join(
            tempfile.gettempdir(), f"visualesuclv-mpv-{uuid.uuid4().hex[:8]}.log"
        )
        try:
            stderr_file = open(self._stderr_log_path, "w")
            self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr_file)
            stderr_file.close()  # el hijo ya tiene su propio descriptor duplicado
        except Exception as exc:
            self.process = None
            self.last_error = str(exc)
            return False

        # Si mpv rechaza algún flag (opción vieja/renombrada, typo, etc.)
        # suele salir casi de inmediato en vez de tardar los 8s completos
        # del timeout del socket — lo detectamos rápido para no dejar
        # una ventana vacía sin explicación.
        time.sleep(0.3)
        early_exit = self.process.poll() is not None
        socket_ok = early_exit or self._wait_for_socket(timeout=8.0)

        if early_exit or not socket_ok:
            self._read_stderr_log()
            if self.process and self.process.poll() is None:
                self.terminate()
            else:
                self.process = None
            self._cleanup_stderr_log()

            if not _retry_minimal:
                # probablemente uno de los flags de caché no es compatible
                # con esta versión de mpv — reintentamos sin ellos. Si el
                # problema era otra cosa (archivo/red), el reintento va a
                # fallar también, pero al menos no perdemos reproducción
                # por un solo flag mal.
                return self.start(url, title=title, audio_only=audio_only, _retry_minimal=True)
            if not self.last_error:
                self.last_error = "mpv no respondió a tiempo (timeout esperando el socket de control)."
            return False

        self._connect()
        self.observe("time-pos")
        self.observe("duration")
        self.observe("playlist-pos")
        self.observe("playlist-count")
        self.observe("eof-reached")
        self.observe("path")
        self._cleanup_stderr_log()
        return True

    def _read_stderr_log(self):
        try:
            with open(self._stderr_log_path, "r", errors="replace") as f:
                self.last_error = f.read().strip()[-800:]
        except Exception:
            pass

    def _cleanup_stderr_log(self):
        try:
            if self._stderr_log_path and os.path.exists(self._stderr_log_path):
                os.remove(self._stderr_log_path)
        except OSError:
            pass

    def load(self, url: str, title: str = ""):
        """Reemplaza el archivo en reproducción sin relanzar el proceso."""
        if not self.is_running():
            self.start(url, title=title)
            return
        self.command(["loadfile", url, "replace"])
        self.set_property("title", title or "Visuales UCLV")
        self.set_property("pause", False)

    # ------------------------------------------------------------------
    # Cola de reproducción (playlist nativa de mpv)
    # ------------------------------------------------------------------
    def load_playlist(self, urls: list[str], start_index: int = 0, title: str = "", audio_only: bool = False):
        """Reemplaza toda la cola de reproducción por `urls`, empezando
        en start_index."""
        if not urls:
            return
        if not self.is_running():
            if not self.start(urls[0], title=title, audio_only=audio_only):
                return
            for u in urls[1:]:
                self.command(["loadfile", u, "append"])
        else:
            self.command(["loadfile", urls[0], "replace"])
            for u in urls[1:]:
                self.command(["loadfile", u, "append"])
        if start_index:
            self.command(["playlist-play-index", start_index])
        self.set_property("pause", False)

    def append_to_playlist(self, url: str):
        self.command(["loadfile", url, "append"])

    def playlist_next(self):
        self.command(["playlist-next", "weak"])

    def playlist_prev(self):
        self.command(["playlist-prev", "weak"])

    def shuffle_playlist(self):
        self.command(["playlist-shuffle"])

    def set_loop_playlist(self, enabled: bool):
        self.set_property("loop-playlist", "inf" if enabled else "no")

    # ------------------------------------------------------------------
    def _wait_for_socket(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.socket_path):
                return True
            if not self.is_running():
                return False
            time.sleep(0.1)
        return False

    def _connect(self):
        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(5.0)
            self._sock.connect(self.socket_path)
            self._sock.settimeout(None)
        except Exception:
            self._sock = None
            return
        self._stop_reading.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
        buf = b""
        while not self._stop_reading.is_set() and self._sock:
            try:
                chunk = self._sock.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle_line(line)

    def _handle_line(self, line: bytes):
        try:
            msg = json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            return
        event = msg.get("event")
        if event == "property-change":
            name = msg.get("name")
            value = msg.get("data")
            if name is not None and value is not None:
                with self._props_lock:
                    self._properties[name] = value
                for cb in self._callbacks.get(name, []):
                    try:
                        cb(value)
                    except Exception:
                        pass
        elif event is not None:
            for cb in self._event_callbacks.get(event, []):
                try:
                    cb(msg)
                except Exception:
                    pass

    def on_event(self, event_name: str, callback):
        """Registra un callback para un evento IPC de mpv (ej. "end-file",
        que mpv manda cuando un archivo termina O falla al cargar). Corre
        en el hilo lector del socket, no en el de la GUI."""
        self._event_callbacks.setdefault(event_name, []).append(callback)

    # ------------------------------------------------------------------
    def command(self, args: list):
        if not self._sock:
            return
        try:
            payload = json.dumps({"command": args}) + "\n"
            with self._send_lock:
                self._sock.sendall(payload.encode("utf-8"))
        except Exception:
            pass

    def set_property(self, name: str, value):
        self.command(["set_property", name, value])

    def observe(self, name: str):
        if name in self._observed:
            return
        self._observed.add(name)
        self.command(["observe_property", self._next_observe_id, name])
        self._next_observe_id += 1

    def on_property(self, name: str, callback):
        """Registra un callback (se llama desde el hilo lector, no la GUI)."""
        self.observe(name)
        self._callbacks.setdefault(name, []).append(callback)

    def get_cached_property(self, name: str, default=0.0):
        with self._props_lock:
            return self._properties.get(name, default)

    # ------------------------------------------------------------------
    def play_pause(self, pause: bool):
        self.set_property("pause", pause)

    def seek(self, seconds: float, absolute: bool = False):
        self.command(["seek", seconds, "absolute" if absolute else "relative"])

    def set_volume(self, value: int):
        self.set_property("volume", value)

    def set_sub_visibility(self, visible: bool):
        self.set_property("sub-visibility", visible)

    def add_subtitle(self, url: str, select: bool = False):
        """Agrega un subtítulo por URL. select=True lo activa de una."""
        self.command(["sub-add", url, "select" if select else "auto"])

    def toggle_fullscreen(self):
        self.command(["cycle", "fullscreen"])

    def terminate(self):
        self._stop_reading.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass
