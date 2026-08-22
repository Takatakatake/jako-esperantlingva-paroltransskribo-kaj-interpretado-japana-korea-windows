#!/usr/bin/env bash
#
# Launch (or cleanly restart) the transcription pipeline with the Web UI on a
# stable port. Activates the repo's venv, stops a previous transcriber
# gracefully, frees the port from stale holders, then starts the CLI.
# Works when installed to ~/bin because it resolves the repo from its own
# real location.

set -euo pipefail

PORT="${PORT:-8765}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
BACKEND="${BACKEND:-speechmatics}"

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[run_transcriber] %s\n' "$*"; }

if [[ -f "$REPO_ROOT/.venv311/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv311/bin/activate"
fi

PYTHON_BIN="python"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    log "python が見つかりません。.venv311 を作成するか python3 をインストールしてください。"
    exit 127
  fi
fi

stop_existing_transcriber() {
  local pids
  pids=$(pgrep -u "$(id -u)" -f "python[^ ]* -m transcriber\.cli" || true)
  if [[ -z "$pids" ]]; then
    return
  fi
  log "既存の文字起こしセッションを停止します (PID: ${pids//$'\n'/ })"
  # SIGINT triggers the graceful shutdown (tail flush + audio restore).
  kill -INT $pids 2>/dev/null || true
  for _ in {1..120}; do
    # return 0 explicitly: under set -e a bare `return` would propagate
    # pgrep's non-zero status and kill the whole script.
    pgrep -u "$(id -u)" -f "python[^ ]* -m transcriber\.cli" >/dev/null 2>&1 || return 0
    sleep 0.25
  done
  log "graceful 停止がタイムアウトしたため強制終了します。"
  kill -9 $pids 2>/dev/null || true
  sleep 0.5
}

cleanup_port() {
  local pids
  pids=$(lsof -t -iTCP:"${PORT}" -sTCP:LISTEN || true)
  if [[ -n "${pids}" ]]; then
    log "ポート ${PORT} を掴んでいるプロセスを閉じます: ${pids//$'\n'/ }"
    echo "${pids}" | xargs -r kill
  fi
  for _ in {1..20}; do
    if lsof -t -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      sleep 0.2
    else
      return 0
    fi
  done
  log "警告: ポート ${PORT} が解放されません。Web UI は次のポートへ退避します。"
}

main() {
  stop_existing_transcriber
  cleanup_port
  log "パイプラインを起動します (port=${PORT}, backend=${BACKEND})"
  exec "$PYTHON_BIN" -m transcriber.cli --backend="${BACKEND}" --log-level="${LOG_LEVEL}"
}

main "$@"
