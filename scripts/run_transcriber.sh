#!/usr/bin/env bash
#
# Launch the transcription pipeline after ensuring the Web UI port is free.
# This guarantees the UI always binds to the primary port (8765) so the browser
# URL stays constant and translations show up immediately.

set -euo pipefail

PORT="${PORT:-8765}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
BACKEND="${BACKEND:-speechmatics}"

cleanup_port() {
  local pids
  pids=$(lsof -t -iTCP:"${PORT}" -sTCP:LISTEN || true)
  if [[ -n "${pids}" ]]; then
    echo "[run_transcriber] Closing listeners on port ${PORT}: ${pids}"
    echo "${pids}" | xargs -r kill
  fi
  for _ in {1..20}; do
    if lsof -t -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      sleep 0.2
    else
      return
    fi
  done
  echo "[run_transcriber] Warning: port ${PORT} still busy; falling back to next attempt."
}

main() {
  cleanup_port
  echo "[run_transcriber] Starting pipeline on port ${PORT} with backend=${BACKEND}"
  python -m transcriber.cli --backend="${BACKEND}" --log-level="${LOG_LEVEL}"
}

main "$@"
