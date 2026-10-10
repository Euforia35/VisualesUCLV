#!/usr/bin/env bash
#
# run.sh — arranca Visuales UCLV instalando automáticamente todo lo
# que falte (Homebrew, mpv, entorno virtual de Python, dependencias).
#
# Uso:
#   chmod +x run.sh      (solo la primera vez)
#   ./run.sh
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

BOLD="\033[1m"; DIM="\033[2m"; GREEN="\033[32m"; YELLOW="\033[33m"; RESET="\033[0m"

info()  { echo -e "${BOLD}➜${RESET} $1"; }
ok()    { echo -e "${GREEN}✓${RESET} $1"; }
warn()  { echo -e "${YELLOW}⚠${RESET} $1"; }

# ----------------------------------------------------------------------
# 1) Homebrew (necesario para instalar mpv)
# ----------------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
    warn "No encontré Homebrew, que hace falta para instalar mpv."
    read -p "¿Instalar Homebrew ahora? [s/N] " resp
    if [[ "$resp" =~ ^[sS]$ ]]; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # En Mac con Apple Silicon, brew queda en /opt/homebrew
        if [[ -x /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
    else
        echo "Sin Homebrew no puedo instalar mpv automáticamente. Instalalo manualmente y volvé a correr este script."
        exit 1
    fi
else
    ok "Homebrew encontrado"
fi

# ----------------------------------------------------------------------
# 2) mpv (motor de reproducción de video)
# ----------------------------------------------------------------------
if ! brew list mpv >/dev/null 2>&1; then
    info "Instalando mpv (esto puede tardar unos minutos)…"
    brew install mpv
else
    ok "mpv ya está instalado"
fi

# ----------------------------------------------------------------------
# 3) Python 3
# ----------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    info "Instalando python3 con Homebrew…"
    brew install python@3.12
else
    ok "python3 encontrado ($(python3 --version))"
fi

# ----------------------------------------------------------------------
# 4) Entorno virtual + dependencias de Python
# ----------------------------------------------------------------------
if [[ ! -d "venv" ]]; then
    info "Creando entorno virtual…"
    python3 -m venv venv
fi

source venv/bin/activate

info "Revisando/instalando dependencias de Python (rápido si ya están)…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
ok "Dependencias listas"

# ----------------------------------------------------------------------
# 5) Arrancar la app
# ----------------------------------------------------------------------
info "Abriendo Visuales UCLV…"
python3 main.py
