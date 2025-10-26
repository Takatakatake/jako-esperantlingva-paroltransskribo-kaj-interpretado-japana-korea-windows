#!/usr/bin/env bash
#
# Ensure PipeWire's default *source* stays on the analog monitor so
# Meet/Zoom loopback audio is always available for transcription.
# If SINK_NAME is provided, it will also pin the default sink,
# otherwise the user's desktop settings are left untouched.

set -euo pipefail

SINK_NAME="${SINK_NAME:-}"
SOURCE_NAME="${SOURCE_NAME:-alsa_output.pci-0000_00_1f.3.analog-stereo.monitor}"

log() {
  printf '[wp-force-monitor] %s\n' "$*" >&2
}

ensure_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "Required command '$1' not found."
    exit 1
  fi
}

ensure_command pactl

if [[ -n "$SINK_NAME" ]]; then
  log "Setting default sink to %s" "$SINK_NAME"
  pactl set-default-sink "$SINK_NAME"
  pactl set-sink-mute "$SINK_NAME" 0 || true
else
  log "Skipping default sink override (SINK_NAME not set)."
fi

log "Setting default source to %s" "$SOURCE_NAME"
pactl set-default-source "$SOURCE_NAME"
pactl set-source-mute "$SOURCE_NAME" 0 || true

log "Done."
