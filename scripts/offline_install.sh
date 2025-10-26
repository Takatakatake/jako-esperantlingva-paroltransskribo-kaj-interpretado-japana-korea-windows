#!/usr/bin/env bash
set -euo pipefail

# Create venv and install from local wheelhouse without network.
# Usage: scripts/offline_install.sh [VENV_DIR] [WHEELHOUSE]

VENV_DIR=${1:-.venv311}
WH=${2:-wheelhouse}
PY=${PYTHON:-python3.11}

if [ ! -d "$WH" ]; then
  echo "[!] wheelhouse ($WH) not found. Run scripts/offline_prepare_wheels.sh first." >&2
  exit 1
fi

"$PY" -m venv "$VENV_DIR"
source "$VENV_DIR"/bin/activate
python -m pip install --upgrade pip
pip install --no-index --find-links "$WH" -r requirements.txt

echo "[+] Offline install complete. Activate with: source $VENV_DIR/bin/activate"

