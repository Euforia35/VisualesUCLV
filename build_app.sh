#!/usr/bin/env bash
#
# build_app.sh — genera "Visuales UCLV.app", una app de doble clic que
# arranca el proyecto usando el entorno virtual ya armado (venv +
# dependencias + mpv). No empaqueta Python ni las librerías dentro del
# .app: simplemente lo lanza, así que evita los dolores de cabeza
# típicos de py2app con PySide6 + mpv.
#
# Uso (una sola vez, desde la carpeta del proyecto):
#   chmod +x build_app.sh
#   ./build_app.sh
#
# Esto crea "Visuales UCLV.app" en el Escritorio. Doble clic para abrir
# de ahí en adelante — no hace falta volver a tocar la Terminal.
#
# Importante: el .app queda apuntando a la carpeta del proyecto en la
# ubicación donde está AHORA MISMO. Si más adelante movés o renombrás
# esta carpeta, hay que volver a correr build_app.sh.
#
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Visuales UCLV"
APP_PATH="$HOME/Desktop/${APP_NAME}.app"

case "$PROJECT_DIR" in
    "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
        echo "⚠ AVISO IMPORTANTE:"
        echo "  El proyecto está dentro de Escritorio, Documentos o Descargas."
        echo "  macOS a veces bloquea en silencio (\"Operation not permitted\")"
        echo "  el acceso de apps sin firmar a esas carpetas protegidas."
        echo ""
        echo "  Recomendado: sacá la carpeta del proyecto de ahí, por ejemplo:"
        echo "    mv \"$PROJECT_DIR\" \"$HOME/VisualesUCLV\""
        echo "  y volvé a correr build_app.sh desde la nueva ubicación."
        echo ""
        read -p "¿Continuar de todas formas acá? [s/N] " resp
        if [[ ! "$resp" =~ ^[sS]$ ]]; then
            exit 1
        fi
        ;;
esac

if [[ ! -d "$PROJECT_DIR/venv" ]]; then
    echo "⚠ No encuentro el entorno virtual (venv) en $PROJECT_DIR."
    echo "  Corré primero ./run.sh al menos una vez para instalar todo."
    exit 1
fi

APP_VERSION="$(cat "$PROJECT_DIR/VERSION" 2>/dev/null || echo "0.0.0")"

echo "➜ Creando $APP_PATH (versión $APP_VERSION) …"
rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

cat > "$APP_PATH/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>cu.uclv.visuales.launcher</string>
    <key>CFBundleVersion</key>
    <string>${APP_VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${APP_VERSION}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>

    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

cat > "$APP_PATH/Contents/MacOS/launcher" << LAUNCHER
#!/usr/bin/env bash
# Generado por build_app.sh — se regenera cada vez que lo corrés de nuevo.
PROJECT_DIR="${PROJECT_DIR}"
LOG_FILE="/tmp/visualesuclv-launch.log"

fail() {
    local msg
    msg="\$(printf '%s' "\$1" | tr '\n' ' ' | sed "s/\\"/'/g")"
    /usr/bin/osascript -e "display alert \\"Visuales UCLV no pudo abrir\\" message \\"\$msg\\" as critical" >/dev/null 2>&1
    exit 1
}

if [[ ! -d "\$PROJECT_DIR" ]]; then
    fail "No se encontró la carpeta del proyecto en \$PROJECT_DIR. Si la moviste o renombraste, volvé a correr build_app.sh desde la nueva ubicación."
fi

cd "\$PROJECT_DIR" || fail "No se pudo entrar a la carpeta \$PROJECT_DIR"

if [[ ! -f venv/bin/activate ]]; then
    fail "No se encontró el entorno virtual (venv) en \$PROJECT_DIR. Abrí Terminal, entrá a esa carpeta, y corré ./run.sh primero."
fi

source venv/bin/activate

python3 main.py > "\$LOG_FILE" 2>&1
STATUS=\$?

if [[ \$STATUS -ne 0 ]]; then
    fail "\$(tail -c 1200 "\$LOG_FILE")"
fi
LAUNCHER

chmod +x "$APP_PATH/Contents/MacOS/launcher"

# Genera el ícono (.icns) a partir de icon.png usando las herramientas
# nativas de macOS (sips + iconutil). Si por algún motivo no están
# disponibles, la app igual funciona, solo que con el ícono genérico.
if [[ -f "$PROJECT_DIR/icon.png" ]] && command -v sips >/dev/null && command -v iconutil >/dev/null; then
    echo "➜ Generando ícono…"
    ICONSET_DIR="$(mktemp -d)/AppIcon.iconset"
    mkdir -p "$ICONSET_DIR"
    for size in 16 32 128 256 512; do
        sips -z $size $size "$PROJECT_DIR/icon.png" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z $double $double "$PROJECT_DIR/icon.png" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET_DIR" -o "$APP_PATH/Contents/Resources/AppIcon.icns"
    rm -rf "$(dirname "$ICONSET_DIR")"
else
    echo "⚠ No se generó ícono personalizado (falta icon.png o sips/iconutil); se usa el genérico."
fi

# Quita el atributo de cuarentena que macOS pone a lo creado por scripts,
# para que no aparezca la advertencia de "desarrollador no identificado"
# la primera vez que se abre.
xattr -cr "$APP_PATH" 2>/dev/null || true

echo "✓ Listo: \"${APP_NAME}.app\" (v${APP_VERSION}) está en tu Escritorio."
echo "  Doble clic para abrir. Si igual aparece una advertencia de macOS"
echo "  la primera vez, hacé clic derecho sobre la app › Abrir."
