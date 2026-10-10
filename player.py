"""
player.py
---------
Widget de "control remoto" para mpv. mpv corre como PROCESO APARTE
(ver mpv_ipc.py) — abre su propia ventana nativa de video, totalmente
independiente del proceso de la app. Este widget solo manda comandos
(play/pausa/seek/volumen/subtítulos/pantalla completa) y muestra el
progreso recibido por el socket IPC.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSlider, QLabel, QPushButton
)

from mpv_ipc import MpvIPCPlayer, find_mpv_binary

MPV_AVAILABLE = find_mpv_binary() is not None


def _fmt_time(seconds: float) -> str:
    if seconds is None or seconds < 0:
        seconds = 0
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


class PlayerWidget(QWidget):
    """Panel de control remoto para la ventana de video de mpv."""

    error_occurred = Signal(str)
    track_finished = Signal()  # el video llegó al final de verdad (no pausa)
    online_subtitles_requested = Signal()

    IDLE_TEXT = (
        "🎬 El video se abre en una ventana aparte de mpv.\n"
        "Usá estos controles para manejarlo desde acá, o los atajos "
        "normales de mpv (espacio para pausar, flechas para saltar)."
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0
        self._seeking = False
        self._mpv: MpvIPCPlayer | None = None
        self._current_title = ""
        self.current_url: str | None = None
        self._pending_start_at = 0.0
        self._paused = False
        self._eof_callback_registered = False

        self._build_ui()

        if not MPV_AVAILABLE:
            self.info_label.setText(
                "No se encontró el programa 'mpv'. Instalalo con:\n"
                "brew install mpv\n"
                "y reiniciá la app."
            )
            self._set_controls_enabled(False)

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.info_label = QLabel(self.IDLE_TEXT)
        self.info_label.setObjectName("nowPlayingLabel")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(self.info_label)

        controls = QWidget(self)
        controls.setObjectName("playerControls")
        c_layout = QVBoxLayout(controls)
        c_layout.setContentsMargins(14, 10, 14, 10)
        c_layout.setSpacing(4)

        seek_row = QHBoxLayout()
        self.time_label = QLabel("0:00")
        self.time_label.setFixedWidth(48)
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderPressed.connect(self._on_seek_start)
        self.seek_slider.sliderReleased.connect(self._on_seek_end)
        self.duration_label = QLabel("0:00")
        self.duration_label.setFixedWidth(48)
        seek_row.addWidget(self.time_label)
        seek_row.addWidget(self.seek_slider, stretch=1)
        seek_row.addWidget(self.duration_label)
        c_layout.addLayout(seek_row)

        btn_row = QHBoxLayout()
        self.play_btn = QPushButton("⏸")
        self.play_btn.setFixedWidth(40)
        self.play_btn.clicked.connect(self.toggle_pause)

        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setFixedWidth(40)
        self.stop_btn.clicked.connect(self.stop)

        self.back10_btn = QPushButton("«10s")
        self.back10_btn.clicked.connect(lambda: self.seek_relative(-10))
        self.fwd10_btn = QPushButton("10s»")
        self.fwd10_btn.clicked.connect(lambda: self.seek_relative(10))

        self.sub_btn = QPushButton("CC")
        self.sub_btn.setCheckable(True)
        self.sub_btn.setChecked(True)
        self.sub_btn.clicked.connect(self._toggle_subs)

        self.online_subs_btn = QPushButton("🌐 Subtítulos")
        self.online_subs_btn.setToolTip("Buscar subtítulos online (OpenSubtitles)")
        self.online_subs_btn.clicked.connect(self.online_subtitles_requested.emit)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        self.fullscreen_btn = QPushButton("⛶ Pantalla completa")
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)

        for w in (self.play_btn, self.stop_btn, self.back10_btn, self.fwd10_btn, self.sub_btn, self.online_subs_btn):
            btn_row.addWidget(w)
        btn_row.addStretch(1)
        btn_row.addWidget(QLabel("🔊"))
        btn_row.addWidget(self.volume_slider)
        btn_row.addWidget(self.fullscreen_btn)
        c_layout.addLayout(btn_row)

        layout.addWidget(controls)
        layout.addStretch(1)

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(400)
        self._ui_timer.timeout.connect(self._refresh_ui)
        self._ui_timer.start()

    def _set_controls_enabled(self, enabled: bool):
        for w in (
            self.play_btn, self.stop_btn, self.back10_btn, self.fwd10_btn,
            self.sub_btn, self.online_subs_btn, self.volume_slider,
            self.fullscreen_btn, self.seek_slider,
        ):
            w.setEnabled(enabled)

    # ------------------------------------------------------------------
    def _ensure_mpv(self) -> bool:
        if self._mpv and self._mpv.is_running():
            return True
        if not MPV_AVAILABLE:
            self.error_occurred.emit(
                "No se encontró el programa 'mpv'. Instalalo con: brew install mpv"
            )
            return False
        self._mpv = MpvIPCPlayer()
        self._eof_callback_registered = False
        return True

    # ------------------------------------------------------------------
    def play(self, url: str, title: str = "", start_at: float = 0.0, subtitle_urls: list | None = None):
        if not self._ensure_mpv():
            return
        self._current_title = title or url
        self.current_url = url
        self.info_label.setText(f"🎬 Reproduciendo en la ventana de mpv:\n{self._current_title}")
        self._paused = False
        self.play_btn.setText("⏸")

        already_running = self._mpv.is_running()
        if already_running:
            self._mpv.load(url, title=self._current_title)
        else:
            ok = self._mpv.start(url, title=self._current_title)
            if not ok:
                detail = self._mpv.last_error or "(mpv no dio más detalles)"
                self.error_occurred.emit(
                    f"No se pudo arrancar mpv.\n\nDetalle: {detail}"
                )
                return

        if not self._eof_callback_registered:
            # "eof-reached" ya lo observa mpv_ipc.start() por defecto;
            # acá solo enganchamos nuestro callback (una sola vez por
            # proceso de mpv, no en cada video, para no acumular
            # callbacks duplicados al pasar de un video a otro).
            self._mpv.on_property("eof-reached", self._on_eof_reached)
            # "end-file" con reason="error" es como mpv avisa que un
            # archivo puntual falló al cargar DESPUÉS de que el proceso
            # ya arrancó bien (ej. timeout de red con el servidor) — a
            # diferencia de una falla de arranque, esto antes quedaba en
            # silencio total (ventana de mpv vacía, sin ningún aviso).
            self._mpv.on_event("end-file", self._on_end_file)
            self._eof_callback_registered = True

        if subtitle_urls:
            for i, sub_url in enumerate(subtitle_urls):
                self._mpv.add_subtitle(sub_url, select=(i == 0))

        if start_at and start_at > 1:
            self._pending_start_at = start_at
            self._arm_resume_seek()

    def _on_end_file(self, msg: dict):
        # Corre en el hilo lector del socket IPC (no el de la GUI);
        # emitir una señal de Qt desde acá es seguro (se encola solo).
        if msg.get("reason") == "error":
            self.error_occurred.emit(
                "mpv no pudo cargar este video.\n\n"
                "Suele ser un problema de conexión con el servidor "
                "(lento o caído en este momento). Probá de nuevo en un "
                "rato, o con otro video para descartar que sea un "
                "archivo puntual."
            )

    def _on_eof_reached(self, value):
        # Corre en el hilo lector del socket IPC (no el hilo de la GUI).
        # Emitir una señal de Qt desde otro hilo es seguro: se entrega
        # encolada al hilo principal automáticamente.
        if value:
            self.track_finished.emit()

    def _arm_resume_seek(self):
        def _on_seekable(value):
            if value and self._pending_start_at:
                self._mpv.seek(self._pending_start_at, absolute=True)
                self._pending_start_at = 0.0

        self._mpv.on_property("seekable", _on_seekable)

    def current_progress(self) -> tuple[float, float]:
        if not self._mpv or not self._mpv.is_running():
            return 0.0, 0.0
        pos = self._mpv.get_cached_property("time-pos", 0.0) or 0.0
        dur = self._mpv.get_cached_property("duration", 0.0) or 0.0
        return pos, dur

    def toggle_pause(self):
        if not self._mpv or not self._mpv.is_running():
            return
        self._paused = not self._paused
        self._mpv.play_pause(self._paused)
        self.play_btn.setText("▶" if self._paused else "⏸")

    def stop(self):
        """Detiene la reproducción y cierra la ventana de mpv."""
        if self._mpv:
            self._mpv.terminate()
            self._mpv = None
        self.info_label.setText(self.IDLE_TEXT)
        self.seek_slider.setValue(0)
        self.time_label.setText("0:00")
        self.duration_label.setText("0:00")
        self.current_url = None
        self._pending_start_at = 0.0
        self._paused = False
        self.play_btn.setText("⏸")

    def seek_relative(self, seconds: float):
        if self._mpv and self._mpv.is_running():
            self._mpv.seek(seconds, absolute=False)

    def _toggle_subs(self):
        if self._mpv and self._mpv.is_running():
            self._mpv.set_sub_visibility(self.sub_btn.isChecked())

    def add_local_subtitle(self, path: str):
        """Agrega y activa un subtítulo descargado (archivo local)."""
        if self._mpv and self._mpv.is_running():
            self._mpv.add_subtitle(path, select=True)
            self.sub_btn.setChecked(True)
            self._mpv.set_sub_visibility(True)

    def current_title(self) -> str:
        return self._current_title

    def _on_volume_changed(self, value: int):
        if self._mpv and self._mpv.is_running():
            self._mpv.set_volume(value)

    def _toggle_fullscreen(self):
        """Pantalla completa de la ventana de mpv (no de la app)."""
        if self._mpv and self._mpv.is_running():
            self._mpv.toggle_fullscreen()

    def _on_seek_start(self):
        self._seeking = True

    def _on_seek_end(self):
        if self._mpv and self._mpv.is_running() and self._duration:
            frac = self.seek_slider.value() / 1000.0
            self._mpv.seek(frac * self._duration, absolute=True)
        self._seeking = False

    def _refresh_ui(self):
        if not self._mpv or not self._mpv.is_running() or self._seeking:
            return
        pos = self._mpv.get_cached_property("time-pos", 0.0) or 0.0
        dur = self._mpv.get_cached_property("duration", 0.0) or 0.0
        self._duration = dur
        self.time_label.setText(_fmt_time(pos))
        self.duration_label.setText(_fmt_time(dur))
        if dur:
            self.seek_slider.setValue(int((pos / dur) * 1000))

    def shutdown(self):
        self._ui_timer.stop()
        if self._mpv:
            self._mpv.terminate()
            self._mpv = None
