#!/usr/bin/env bash
set -euo pipefail

# Pre-download wheels for offline install into ./wheelhouse
# This is OS/arch specific and may be large (hundreds of MB).

VENV_DIR=${1:-.venv311}
WH=${2:-wheelhouse}

mkdir -p "$WH"
source "$VENV_DIR"/bin/activate

echo "[+] Downloading wheels for requirements.txt into $WH"
pip download -r requirements.txt -d "$WH"

echo "[+] Wheelhouse prepared at $WH. Offline install with scripts/offline_install.sh"

