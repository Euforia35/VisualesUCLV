#!/usr/bin/env python3
"""
Visuales UCLV — cliente de escritorio para macOS
--------------------------------------------------
Explora y reproduce en streaming el contenido de visuales.uclv.cu
sin pasar por el navegador.

Ejecutar en modo desarrollo:
    python3 main.py

Empaquetar como app de macOS (doble clic):
    python3 setup.py py2app
"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Visuales UCLV")

    qss_path = Path(__file__).parent / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text())

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
