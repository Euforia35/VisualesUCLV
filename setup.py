"""
setup.py — empaqueta la app con py2app.

Uso:
    pip3 install py2app
    python3 setup.py py2app

El resultado queda en dist/VisualesUCLV.app — doble clic para abrir.
"""

from pathlib import Path
from setuptools import setup

VERSION = (Path(__file__).parent / "VERSION").read_text().strip()

APP = ["main.py"]
DATA_FILES = ["style.qss"]
OPTIONS = {
    "argv_emulation": False,
    "includes": [
        "apache_index", "player", "main_window", "progress", "thumbnails",
        "downloads", "mpv_ipc", "movie_detail", "music_view", "audio_metadata",
        "lyrics", "config_store", "opensubtitles", "tmdb", "online_dialogs",
        "history_view", "catalog", "search_dialog", "chromecast_sender",
    ],
    "resources": ["style.qss"],
    "plist": {
        "CFBundleName": "Visuales UCLV",
        "CFBundleDisplayName": "Visuales UCLV",
        "CFBundleIdentifier": "cu.uclv.visuales.desktop",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "NSAppTransportSecurity": {
            # El sitio se sirve por HTTP plano, no HTTPS.
            "NSAllowsArbitraryLoads": True
        },
    },
}

if Path("icon.icns").exists():
    OPTIONS["iconfile"] = "icon.icns"

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
