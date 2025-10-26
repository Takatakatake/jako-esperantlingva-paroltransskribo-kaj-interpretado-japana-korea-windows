#!/usr/bin/env bash
set -euo pipefail

# Create Python 3.11 venv and install runtime deps from requirements.txt
# Usage: scripts/bootstrap_env.sh [VENV_DIR]

VENV_DIR=${1:-.venv311}
PY=${PYTHON:-python3.11}

if [ ! -d "$VENV_DIR" ]; then
  echo "[+] Creating venv at $VENV_DIR"
  "$PY" -m venv "$VENV_DIR"
fi

source "$VENV_DIR"/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "[+] Done. Activate with: source $VENV_DIR/bin/activate"

