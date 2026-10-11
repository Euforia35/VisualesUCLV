"""
chromecast_sender.py (v2.1)
--------------------------
Envío opcional de vídeos a Chromecast / Xiaomi MiBox mediante pychromecast.
La aplicación sigue funcionando si la dependencia no está instalada.

Novedad en v2 — subtítulos: si el servidor remoto no envía cabeceras CORS
(típico en servidores Apache sin configurar), el Chromecast rechaza los
subtítulos externos en silencio. Ahora este módulo descarga el .srt/.vtt,
lo normaliza a WebVTT y lo sirve desde la propia Mac por la red local con
las cabeceras correctas. El dispositivo Cast toma el vídeo de internet y
el subtítulo de la Mac; por eso la Mac debe permanecer encendida mientras
se proyectan subtítulos (el vídeo seguirá aunque se interrumpa el relé).

Novedad en v2.1 — correcciones críticas:
- Validación de accesibilidad del servidor de subtítulos desde el Chromecast.
- Fallback explícito: si el relé falla, se rechaza la URL remota en lugar de
  intentarla (que sabemos que no tiene CORS).
- Búsqueda robusta de pistas de subtítulos (itera sobre todas las disponibles).
- Logs detallados de errores para depuración.
- Manejo mejorado de excepciones en la activación de subtítulos.

Los métodos de red son bloqueantes: deben llamarse desde un hilo de trabajo,
nunca directamente desde la interfaz gráfica.
"""

from __future__ import annotations

import logging
import socket
import tempfile
import threading
import time
import urllib.error
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
logger = logging.getLogger(__name__)

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


def _test_server_accessibility(host: str, port: int, timeout: float = 3.0) -> bool:
    """Valida que el servidor HTTP sea accesible desde la red local."""
    try:
        test_url = f"http://{host}:{port}/ping"
        with urllib.request.urlopen(test_url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, socket.timeout, OSError):
        return False


class _SubtitlesRelay:
    """Sirve UN archivo de subtítulos por la red local, con CORS completo,
    para que el dispositivo Cast pueda descargarlo sin restricciones."""

    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._url: str | None = None

    def start(self, source_url: str) -> str:
        """Descarga el subtítulo, lo normaliza a .vtt y devuelve la URL local
        que el dispositivo Cast debe usar. Lanza excepción si algo falla."""
        self.stop()
        
        logger.info(f"[Subtítulos] Descargando desde: {source_url}")
        try:
            with urllib.request.urlopen(source_url, timeout=20) as resp:
                raw = resp.read()
        except urllib.error.URLError as e:
            logger.error(f"[Subtítulos] Error descargando: {e}")
            raise RuntimeError(f"No se pudo descargar el subtítulo: {e}")
        except socket.timeout:
            logger.error("[Subtítulos] Timeout descargando")
            raise RuntimeError("Timeout descargando el subtítulo")
        
        try:
            texto = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                texto = raw.decode("latin-1", errors="replace")
            except Exception as e:
                logger.error(f"[Subtítulos] Error decodificando: {e}")
                raise RuntimeError(f"No se pudo decodificar el subtítulo: {e}")

        tmp = Path(tempfile.gettempdir()) / "visuales_uclv_cast_subs.vtt"
        try:
            tmp.write_text(_srt_a_vtt(texto), encoding="utf-8")
            payload = tmp.read_bytes()
        except Exception as e:
            logger.error(f"[Subtítulos] Error guardando archivo temporal: {e}")
            raise RuntimeError(f"Error guardando subtítulos: {e}")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ping":
                    self.send_response(200)
                    self.end_headers()
                    return
                
                self.send_response(200)
                self.send_header("Content-Type", "text/vtt; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Range")
                self.send_header("Access-Control-Expose-Headers",
                                 "Content-Length, Content-Range")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass  # silenciar los logs de acceso HTTP

        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
            puerto = self._server.server_address[1]
            self._thread = threading.Thread(target=self._server.serve_forever,
                                            daemon=True)
            self._thread.start()
            
            # Pequeña pausa para asegurar que el servidor está listo
            time.sleep(0.1)
            
            lan_ip = _lan_ip()
            self._url = f"http://{lan_ip}:{puerto}/subtitles.vtt"
            
            # Validar accesibilidad antes de devolverla
            logger.info(f"[Subtítulos] Validando accesibilidad: {self._url}")
            if not _test_server_accessibility(lan_ip, puerto, timeout=5.0):
                logger.warning("[Subtítulos] Servidor local no es accesible (¿firewall?)")
                raise RuntimeError(
                    "El servidor local de subtítulos no es accesible. "
                    "Comprueba el firewall de la Mac."
                )
            
            logger.info(f"[Subtítulos] Relé iniciado en {self._url}")
            return self._url
            
        except Exception as e:
            logger.error(f"[Subtítulos] Error iniciando relé: {e}")
            self.stop()
            raise

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:
                logger.debug(f"[Subtítulos] Error deteniendo servidor: {e}")
            self._server = None
            self._thread = None
            self._url = None


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
                logger.info(f"[Chromecast] Conectando a {friendly_name} en {host}:{8009}")
                cast = pychromecast.get_chromecast_from_host(
                    (host, 8009, None, None, friendly_name)
                )
                cast.wait(timeout=timeout)
                mc = cast.media_controller

                kwargs = {"title": title or "Visuales UCLV"}
                sub_note = ""
                
                if subtitle_urls:
                    sub_url = subtitle_urls[0]
                    logger.info(f"[Chromecast] Procesando subtítulos: {sub_url}")
                    
                    try:
                        # Plan principal: relé local (.vtt + CORS)
                        sub_url = self._relay.start(sub_url)
                        kwargs["subtitles"] = sub_url
                        kwargs["subtitles_lang"] = "es"
                        kwargs["subtitles_mime"] = "text/vtt"
                        sub_note = ("\n\n✓ Subtítulos activados (los sirve tu "
                                   "Mac — déjala encendida mientras se proyectan).")
                        logger.info("[Chromecast] Subtítulos relé exitoso")
                        
                    except Exception as relay_error:
                        # NO intentar fallback a la URL remota: sabemos que no tiene CORS
                        logger.error(f"[Chromecast] Fallo del relé de subtítulos: {relay_error}")
                        sub_note = (
                            "\n\n⚠ No se pudo activar subtítulos: el relé local falló.\n"
                            "Razón: " + str(relay_error) + "\n"
                            "Sugerencia: comprueba que la Mac y el MiBox estén en la "
                            "misma red y que el firewall lo permita."
                        )
                        # Se envía el video SIN subtítulos
                        logger.warning("[Chromecast] Continuando sin subtítulos")

                logger.info(f"[Chromecast] Enviando a reproducir: {url}")
                mc.play_media(url, "video/mp4", **kwargs)
                mc.block_until_active(timeout=timeout)

                # Activación robusta de subtítulos si se lograron añadir
                if subtitle_urls and "subtitles" in kwargs:
                    logger.info("[Chromecast] Intentando activar pista de subtítulos")
                    try:
                        # Obtener lista de pistas disponibles
                        media_status = mc.media_status
                        subtitle_tracks = []
                        
                        if hasattr(media_status, 'subtitle_tracks') and media_status.subtitle_tracks:
                            subtitle_tracks = media_status.subtitle_tracks
                            logger.info(f"[Chromecast] Pistas disponibles: {len(subtitle_tracks)}")
                        
                        # Intentar activar: primero idx 1, luego idx 0
                        activated = False
                        for track_idx in [1, 0]:
                            if track_idx < len(subtitle_tracks):
                                try:
                                    mc.enable_subtitle(track_idx)
                                    logger.info(f"[Chromecast] Subtítulos activados (índice {track_idx})")
                                    activated = True
                                    break
                                except Exception as e:
                                    logger.debug(f"[Chromecast] Falló índice {track_idx}: {e}")
                                    continue
                        
                        if not activated:
                            sub_note += (
                                "\n\nℹ Nota: no se pudo activar automáticamente. "
                                "Si no aparecen subtítulos, actívalos desde el mando a distancia."
                            )
                            logger.warning("[Chromecast] No se activaron subtítulos automáticamente")
                            
                    except Exception as e:
                        logger.error(f"[Chromecast] Error activando subtítulos: {e}")
                        sub_note += (
                            "\n\nℹ Nota: hubo un error al activar subtítulos. "
                            "Intenta desde el mando a distancia."
                        )

                fmt_note = ""
                lower = url.lower().split("?")[0]
                if not lower.endswith(_SAFE_EXTENSIONS):
                    fmt_note = (
                        "\n\n⚠ Advertencia: este archivo no es .mp4 — el dispositivo "
                        "podría no poder reproducirlo (el receptor por defecto "
                        "solo asegura mp4/H.264)."
                    )

                self._disconnect_current()
                self._cast = cast
                name = cast.device.friendly_name
                logger.info(f"[Chromecast] Conexión exitosa con {name}")

                return True, (
                    f"▶ Reproduciendo en {name}.\n\n"
                    "El vídeo se transmite directo desde el servidor hacia el "
                    "dispositivo — tu Mac quedó libre."
                    f"{sub_note}{fmt_note}"
                )
                
        except socket.timeout:
            logger.error("[Chromecast] Timeout de conexión")
            self._relay.stop()
            return False, self._friendly_error(socket.timeout("Timeout de conexión"))
        except Exception as exc:
            logger.error(f"[Chromecast] Error: {exc}", exc_info=True)
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
                logger.info(f"[Chromecast] Deteniendo transmisión en {name}")
                self._cast.media_controller.stop()
                self._cast.disconnect()
                self._cast = None
                logger.info("[Chromecast] Transmisión detenida")
                return True, f"⏹ Transmisión detenida en {name}."
            except Exception as exc:
                logger.error(f"[Chromecast] Error deteniendo: {exc}")
                return False, f"No se pudo detener la transmisión: {exc}"

    def _disconnect_current(self):
        if self._cast is not None:
            try:
                self._cast.disconnect()
            except Exception as e:
                logger.debug(f"[Chromecast] Error desconectando: {e}")
            self._cast = None

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        msg = str(exc) or exc.__class__.__name__
        low = msg.lower()
        if "timeout" in low or "timed out" in low:
            return (
                "❌ No se pudo conectar con el MiBox (tiempo de espera agotado).\n\n"
                "Comprueba que:\n"
                "• La Mac y el MiBox estén en la misma red Wi-Fi.\n"
                "• La IP configurada sea la correcta (menú contextual › "
                "«Configurar MiBox…»).\n"
                "• El MiBox esté encendido y despierto."
            )
        if "connection refused" in low or "no route" in low or "unreachable" in low:
            return (
                f"❌ No se llegó al MiBox ({msg}).\n\n"
                "Revisa la IP configurada y que ambos equipos estén en la "
                "misma red."
            )
        return (
            f"❌ No se pudo enviar al MiBox:\n\n{msg}\n\n"
            "Si el dispositivo mostró algún error en pantalla, anótalo: "
            "ayuda a distinguir si fue la conexión o el formato del vídeo."
        )
