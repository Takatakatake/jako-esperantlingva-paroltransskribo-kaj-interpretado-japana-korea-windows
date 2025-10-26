#!/usr/bin/env bash
#
# Prepare the local environment so that `python -m transcriber.cli` can bind the
# Caption Web UI port (default 8765) on the very first try.
# 1. Terminates lingering CLI processes.
# 2. Closes any LISTEN sockets on the target port.
# 3. Waits until the port is truly free.

set -euo pipefail

PORT="${PORT:-8765}"
CLI_PATTERN="${CLI_PATTERN:-python -m transcriber.cli}"
WAIT_LOOPS=25
WAIT_SLEEP=0.2

log() {
  printf '[prep_webui] %s\n' "$*" >&2
}

kill_cli_processes() {
  if pgrep -f "${CLI_PATTERN}" >/dev/null 2>&1; then
    log "Terminating existing CLI processes matching: ${CLI_PATTERN}"
    pkill -f "${CLI_PATTERN}" || true
    sleep "${WAIT_SLEEP}"
  fi
}

kill_port_listeners() {
  local pids
  pids=$(lsof -t -iTCP:"${PORT}" -sTCP:LISTEN || true)
  if [[ -n "${pids}" ]]; then
    log "Closing listeners on port ${PORT}: ${pids}"
    echo "${pids}" | xargs -r kill
  fi
}

wait_until_free() {
  for _ in $(seq 1 "${WAIT_LOOPS}"); do
    if lsof -t -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      sleep "${WAIT_SLEEP}"
    else
      log "Port ${PORT} is free."
      return
    fi
  done
  log "Warning: port ${PORT} still appears busy; check running processes manually."
}

main() {
  kill_cli_processes
  kill_port_listeners
  wait_until_free
}

main "$@"
