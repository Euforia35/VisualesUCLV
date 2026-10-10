#!/usr/bin/env bash
#
# build_dmg.sh — genera un .dmg de Visuales UCLV que podés llevar a
# OTRA Mac SIN Python instalado. A diferencia de build_app.sh (que
# arma un lanzador liviano que depende del venv de ESTA carpeta),
# este script usa py2app para empaquetar el intérprete de Python y
# todas las librerías (PySide6, requests, Pillow, mutagen, etc.)
# DENTRO del .app.
#
# Lo único que la Mac destino SIGUE necesitando por su cuenta es el
# programa `mpv` (via `brew install mpv`) — es un proceso aparte, no
# una librería de Python, así que py2app no puede empaquetarlo. Es un
# solo comando de instalación, mucho más liviano que pedirle a alguien
# que instale Python + todo lo demás.
#
# Uso (desde la carpeta del proyecto, con el venv ya armado):
#   chmod +x build_dmg.sh
#   ./build_dmg.sh
#
# Puede tardar varios minutos la primera vez (py2app analiza y copia
# todas las dependencias). El resultado es "Visuales UCLV.dmg" en esta
# misma carpeta.
#
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

APP_VERSION="$(cat VERSION 2>/dev/null || echo "0.0.0")"
APP_NAME="Visuales UCLV"
DMG_NAME="${APP_NAME}-${APP_VERSION}.dmg"

if [[ ! -d "venv" ]]; then
    echo "⚠ No encuentro el entorno virtual (venv) en esta carpeta."
    echo "  Corré primero ./run.sh al menos una vez para instalar todo."
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate

if ! python3 -c "import py2app" 2>/dev/null; then
    echo "➜ Instalando py2app…"
    pip install py2app
fi

# Genera el ícono .icns si no existe todavía (mismo proceso que build_app.sh)
if [[ -f "icon.png" ]] && [[ ! -f "icon.icns" ]] && command -v sips >/dev/null && command -v iconutil >/dev/null; then
    echo "➜ Generando ícono…"
    ICONSET_DIR="$(mktemp -d)/AppIcon.iconset"
    mkdir -p "$ICONSET_DIR"
    for size in 16 32 128 256 512; do
        sips -z $size $size icon.png --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z $double $double icon.png --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET_DIR" -o icon.icns
    rm -rf "$(dirname "$ICONSET_DIR")"
fi

echo "➜ Empaquetando con py2app (puede tardar varios minutos)…"
rm -rf build dist
python3 setup.py py2app

APP_BUNDLE="dist/${APP_NAME}.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
    echo "✗ py2app no generó el .app esperado en $APP_BUNDLE — revisá los errores de arriba."
    exit 1
fi

echo "➜ Armando el .dmg…"
STAGING_DIR="$(mktemp -d)/dmg-staging"
mkdir -p "$STAGING_DIR"
cp -R "$APP_BUNDLE" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"

rm -f "$DMG_NAME"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING_DIR" -ov -format UDZO "$DMG_NAME"
rm -rf "$(dirname "$STAGING_DIR")"

echo ""
echo "✓ Listo: $DMG_NAME"
echo "  Se lo podés pasar a cualquier Mac. Ahí, doble clic para montarlo,"
echo "  y arrastrar \"${APP_NAME}.app\" al ícono de Applications."
echo ""
echo "  Esa otra Mac va a necesitar igual tener 'mpv' instalado"
echo "  (brew install mpv) — es lo único que py2app no puede empaquetar"
echo "  adentro, porque es un programa aparte, no una librería de Python."
