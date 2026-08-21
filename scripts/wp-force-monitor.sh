#!/usr/bin/env bash
#
# Pin PipeWire's default *source* to a monitor so Meet/Zoom loopback audio
# stays available for transcription.
#
# SOURCE_NAME selects the monitor explicitly. When unset, the script prefers
# the transcriber's codex_transcribe.monitor (if that sink exists) and
# otherwise falls back to the current default sink's monitor — it never
# assumes a specific machine's device name.
# If SINK_NAME is provided, it will also pin the default sink; otherwise the
# user's desktop settings are left untouched.
#
# NOTE: do not enable the systemd units for this script while using the CLI's
# automatic loopback setup (python -m transcriber.cli); both would fight over
# the default source.

set -euo pipefail

SINK_NAME="${SINK_NAME:-}"
SOURCE_NAME="${SOURCE_NAME:-}"

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

codex_routing_is_live() {
  # A codex_transcribe sink counts only when it is actually in use: either it
  # is the default sink, or a loopback is draining its monitor. A stale
  # leftover sink receives no audio, so its monitor would capture silence.
  local default_sink="$1"
  if [[ "$default_sink" == codex_transcribe* ]]; then
    return 0
  fi
  pactl list short modules | grep 'module-loopback' | grep -q 'source=codex_transcribe\.monitor'
}

if [[ -z "$SOURCE_NAME" ]]; then
  default_sink=$(LC_ALL=C pactl info | sed -n 's/^Default Sink: *//p')
  if codex_routing_is_live "$default_sink"; then
    SOURCE_NAME="codex_transcribe.monitor"
  else
    if [[ -z "$default_sink" ]]; then
      log "Could not detect the default sink; set SOURCE_NAME=<monitor source> explicitly."
      exit 1
    fi
    SOURCE_NAME="${default_sink}.monitor"
  fi
  log "SOURCE_NAME not set; using $SOURCE_NAME"
fi

if ! pactl list short sources | awk '{print $2}' | grep -qx "$SOURCE_NAME"; then
  log "Source '$SOURCE_NAME' does not exist. Available sources:"
  pactl list short sources | awk '{print "  " $2}' >&2
  exit 1
fi

if [[ -n "$SINK_NAME" ]]; then
  log "Setting default sink to $SINK_NAME"
  pactl set-default-sink "$SINK_NAME"
  pactl set-sink-mute "$SINK_NAME" 0 || true
else
  log "Skipping default sink override (SINK_NAME not set)."
fi

log "Setting default source to $SOURCE_NAME"
pactl set-default-source "$SOURCE_NAME"
pactl set-source-mute "$SOURCE_NAME" 0 || true

log "Done."
