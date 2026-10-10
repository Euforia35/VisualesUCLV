"""
chromecast_sender.py (v2)
-------------------------
Envío opcional de vídeos a Chromecast / Xiaomi MiBox mediante pychromecast.
La aplicación sigue funcionando si la dependencia no está instalada.

Novedad en v2 — subtítulos: si el servidor remoto no envía cabeceras CORS
(típico en servidores Apache sin configurar), el Chromecast rechaza los
subtítulos externos en silencio. Ahora este módulo descarga el .srt/.vtt,
lo normaliza a WebVTT y lo sirve desde la propia Mac por la red local con
las cabeceras correctas. El dispositivo Cast toma el vídeo de internet y
el subtítulo de la Mac; por eso la Mac debe permanecer encendida mientras
se proyectan subtítulos (el vídeo seguirá aunque se interrumpa el relé).

Los métodos de red son bloqueantes: deben llamarse desde un hilo de trabajo,
nunca directamente desde la interfaz gráfica.
"""

from __future__ import annotations

import logging
import socket
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import pychromecast
    CHROMECAST_AVAILABLE = True
except ImportError:
    pychromecast = None
    CHROMECAST_AVAILABLE = False

logging.getLogger("zeroconf").setLevel(logging.WARNING)

INSTALL_HINT = (
    "Para enviar a MiBox hace falta instalar una librería extra:\n\n"
    "    python3 -m pip install pychromecast\n\n"
    "Después reinicia la app. El resto de Visuales UCLV funciona sin ella."
)

_SAFE_EXTENSIONS = (".mp4", ".m4v", ".webm")


def _lan_ip() -> str:
    """Dirección IP local de la Mac tal como la ve el dispositivo Cast."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # connect() UDP no envía paquetes; solo selecciona la interfaz de salida.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def _srt_a_vtt(texto: str) -> str:
    """Normaliza subtítulos a WebVTT: cabecera WEBVTT y tiempos coma -> punto.
    Si el archivo ya era VTT, lo deja prácticamente igual."""
    lineas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if "WEBVTT" not in lineas[0].upper():
        lineas.insert(0, "WEBVTT")
        lineas.insert(1, "")
    return "\n".join(l.replace(",", ".") if "-->" in l else l for l in lineas)


class _SubtitlesRelay:
    """Sirve UN archivo de subtítulos por la red local, con CORS completo,
    para que el dispositivo Cast pueda descargarlo sin restricciones."""

    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, source_url: str) -> str:
        """Descarga el subtítulo, lo normaliza a .vtt y devuelve la URL local
        que el dispositivo Cast debe usar. Lanza excepción si algo falla."""
        self.stop()
        with urllib.request.urlopen(source_url, timeout=20) as resp:
            raw = resp.read()
        try:
            texto = raw.decode("utf-8")
        except UnicodeDecodeError:
            texto = raw.decode("latin-1", errors="replace")

        tmp = Path(tempfile.gettempdir()) / "visuales_uclv_cast_subs.vtt"
        tmp.write_text(_srt_a_vtt(texto), encoding="utf-8")
        payload = tmp.read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/vtt; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Range")
                self.send_header("Access-Control-Expose-Headers",
                                 "Content-Length, Content-Range")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass  # silenciar los logs de acceso HTTP

        self._server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        puerto = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        return f"http://{_lan_ip()}:{puerto}/subtitles.vtt"

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
            self._thread = None


class ChromecastSender:
    """Envía una URL de vídeo a un dispositivo Cast y recuerda la última
    conexión para poder detenerla después."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cast = None
        self._relay = _SubtitlesRelay()

    def is_active(self) -> bool:
        return self._cast is not None

    def send(
        self,
        url: str,
        title: str = "",
        subtitle_urls: list | None = None,
        host: str = "192.168.1.40",
        friendly_name: str = "MiBox",
        timeout: float = 10.0,
    ) -> tuple[bool, str]:
        if not CHROMECAST_AVAILABLE:
            return False, INSTALL_HINT

        try:
            with self._lock:
                cast = pychromecast.get_chromecast_from_host(
                    (host, 8009, None, None, friendly_name)
                )
                cast.wait(timeout=timeout)
                mc = cast.media_controller

                kwargs = {"title": title or "Visuales UCLV"}
                sub_note = ""
                if subtitle_urls:
                    sub_url = subtitle_urls[0]
                    try:
                        # Plan principal: relé local (.vtt + CORS). Si falla,
                        # se intenta con la URL remota original.
                        sub_url = self._relay.start(sub_url)
                        sub_note = ("\n\nSubtítulos activados (los sirve tu "
                                    "Mac — déjala encendida mientras se proyectan).")
                    except Exception:
                        sub_note = ("\n\nAviso: no se pudo preparar el subtítulo "
                                    "local; se intentó con el servidor remoto, "
                                    "que podría no tener CORS.")
                    kwargs["subtitles"] = sub_url
                    kwargs["subtitles_lang"] = "es"
                    kwargs["subtitles_mime"] = "text/vtt"

                mc.play_media(url, "video/mp4", **kwargs)
                mc.block_until_active(timeout=timeout)

                if subtitle_urls:
                    try:
                        mc.enable_subtitle(1)
                    except Exception:
                        sub_note = (
                            "\n\nAviso: el dispositivo no aceptó la pista de "
                            "subtítulos automáticamente. Si no aparecen, "
                            "actívalos desde el mando a distancia."
                        )

                fmt_note = ""
                lower = url.lower().split("?")[0]
                if not lower.endswith(_SAFE_EXTENSIONS):
                    fmt_note = (
                        "\n\nAviso: este archivo no es .mp4 — el dispositivo "
                        "podría no poder reproducirlo (el receptor por defecto "
                        "solo asegura mp4/H.264)."
                    )

                self._disconnect_current()
                self._cast = cast
                name = cast.device.friendly_name

            return True, (
                f"▶ Reproduciendo en {name}.\n\n"
                "El vídeo se transmite directo desde el servidor hacia el "
                "dispositivo — tu Mac quedó libre."
                f"{sub_note}{fmt_note}"
            )
        except Exception as exc:
            self._relay.stop()
            return False, self._friendly_error(exc)

    def stop(self) -> tuple[bool, str]:
        if not CHROMECAST_AVAILABLE:
            return False, INSTALL_HINT
        with self._lock:
            self._relay.stop()
            if not self._cast:
                return False, "No hay ninguna transmisión al MiBox en curso."
            try:
                name = self._cast.device.friendly_name
                self._cast.media_controller.stop()
                self._cast.disconnect()
                self._cast = None
                return True, f"⏹ Transmisión detenida en {name}."
            except Exception as exc:
                return False, f"No se pudo detener la transmisión: {exc}"

    def _disconnect_current(self):
        if self._cast is not None:
            try:
                self._cast.disconnect()
            except Exception:
                pass
            self._cast = None

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        msg = str(exc) or exc.__class__.__name__
        low = msg.lower()
        if "timeout" in low or "timed out" in low:
            return (
                "No se pudo conectar con el MiBox (tiempo de espera agotado).\n\n"
                "Comprueba que:\n"
                "• La Mac y el MiBox estén en la misma red Wi-Fi.\n"
                "• La IP configurada sea la correcta (menú contextual › "
                "«Configurar MiBox…»).\n"
                "• El MiBox esté encendido y despierto."
            )
        if "connection refused" in low or "no route" in low or "unreachable" in low:
            return (
                f"No se llegó al MiBox ({msg}).\n\n"
                "Revisa la IP configurada y que ambos equipos estén en la "
                "misma red."
            )
        return (
            f"No se pudo enviar al MiBox:\n\n{msg}\n\n"
            "Si el dispositivo mostró algún error en pantalla, anótalo: "
            "ayuda a distinguir si fue la conexión o el formato del vídeo."
        )
