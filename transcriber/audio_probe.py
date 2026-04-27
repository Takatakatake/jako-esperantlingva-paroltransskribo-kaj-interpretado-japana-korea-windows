"""Short, non-destructive audio capture checks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd

from .config import AudioInputConfig


class AudioProbeError(Exception):
    """Raised when a short audio probe cannot be completed."""


@dataclass(frozen=True)
class AudioProbeResult:
    device_index: Optional[int]
    device_name: str
    sample_rate: int
    channels: int
    duration_seconds: float
    frames_captured: int
    rms_dbfs: float
    peak_dbfs: float
    silent: bool
    clipped: bool


def _dbfs(value: float) -> float:
    if value <= 0:
        return float("-inf")
    return 20.0 * math.log10(value / 32767.0)


def _format_db(value: float) -> str:
    if math.isinf(value):
        return "-inf"
    return f"{value:.1f}"


def _normalise_duration(duration_seconds: float) -> float:
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise AudioProbeError(f"Invalid duration: {duration_seconds!r}") from exc
    if duration <= 0:
        raise AudioProbeError("Audio probe duration must be greater than 0 seconds.")
    return min(duration, 30.0)


def _device_info(device_index: Optional[int]) -> dict:
    try:
        if device_index is None:
            info = sd.query_devices(kind="input")
        else:
            info = sd.query_devices(device_index, kind="input")
    except Exception as exc:  # noqa: BLE001
        raise AudioProbeError(f"Failed to open audio device {device_index}: {exc}") from exc

    if not isinstance(info, dict):
        raise AudioProbeError("sounddevice did not return input device details.")
    if int(info.get("max_input_channels", 0)) <= 0:
        raise AudioProbeError(f"Selected device has no input channels: {info.get('name', device_index)}")
    return info


def probe_audio_levels(
    config: AudioInputConfig,
    duration_seconds: float = 3.0,
    *,
    device_index: Optional[int] = None,
    device_sample_rate: Optional[int] = None,
    channels: Optional[int] = None,
) -> AudioProbeResult:
    """Record a short buffer and report simple level statistics.

    This does not touch system routing or .env. It only opens the selected
    input long enough to tell whether a signal is present.
    """

    duration = _normalise_duration(duration_seconds)
    selected_device = config.device_index if device_index is None else device_index
    info = _device_info(selected_device)

    max_channels = int(info.get("max_input_channels", 1))
    requested_channels = channels if channels is not None else config.channels
    capture_channels = max(1, min(int(requested_channels), max_channels))
    sample_rate = int(device_sample_rate or config.device_sample_rate or config.sample_rate)
    frames = max(1, int(round(sample_rate * duration)))

    try:
        recording = sd.rec(
            frames,
            samplerate=sample_rate,
            channels=capture_channels,
            dtype="int16",
            device=selected_device,
        )
        sd.wait()
    except Exception as exc:  # noqa: BLE001
        raise AudioProbeError(f"Failed to record test audio: {exc}") from exc

    samples = np.asarray(recording, dtype=np.int16)
    if samples.size == 0:
        rms = 0.0
        peak = 0.0
    else:
        if samples.ndim > 1 and samples.shape[1] > 1:
            mono = samples.astype(np.float64).mean(axis=1)
        else:
            mono = samples.reshape(-1).astype(np.float64)
        rms = math.sqrt(float(np.mean(mono * mono))) if mono.size else 0.0
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0

    rms_dbfs = _dbfs(rms)
    peak_dbfs = _dbfs(peak)
    silence_threshold = min(config.level_silence_threshold_dbfs, -1.0)
    clip_threshold = min(config.level_clip_threshold_dbfs, 0.0)

    return AudioProbeResult(
        device_index=selected_device,
        device_name=str(info.get("name", "default input")),
        sample_rate=sample_rate,
        channels=capture_channels,
        duration_seconds=duration,
        frames_captured=frames,
        rms_dbfs=rms_dbfs,
        peak_dbfs=peak_dbfs,
        silent=rms_dbfs <= silence_threshold,
        clipped=peak_dbfs >= clip_threshold,
    )


def render_audio_probe_result(result: AudioProbeResult, profile: str = "current") -> str:
    lines = [
        "======================",
        "Audio level test",
        "======================",
        f"Profile: {profile}",
        f"Device: #{result.device_index} {result.device_name}"
        if result.device_index is not None
        else f"Device: {result.device_name}",
        f"Format: {result.sample_rate} Hz, {result.channels} ch, {result.duration_seconds:.1f}s",
        f"Frames: {result.frames_captured}",
        f"RMS: {_format_db(result.rms_dbfs)} dBFS",
        f"Peak: {_format_db(result.peak_dbfs)} dBFS",
        "",
        "[Result]",
    ]

    if result.clipped:
        lines.append("  - 入力が大きすぎる可能性があります。アプリ音量またはマイクゲインを少し下げてください。")
    elif result.silent:
        lines.append("  - ほぼ無音です。マイクに話すか、PC音声を再生してから再確認してください。")
        lines.append("  - ループバック時は、対象アプリの出力先と PipeWire/PulseAudio monitor source を確認してください。")
    else:
        lines.append("  - 入力信号を検出しました。このデバイスは音を拾えています。")

    lines.append("")
    lines.append("このテストはOS側の既定デバイス、音量ミキサー、.envを変更しません。")
    return "\n".join(lines)
