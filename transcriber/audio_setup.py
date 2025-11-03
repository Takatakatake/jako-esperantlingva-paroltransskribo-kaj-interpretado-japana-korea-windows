"""Helpers for preparing and diagnosing audio capture environments."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

import sounddevice as sd

from .config import AudioCaptureMode, AudioInputConfig


class AudioEnvironmentError(Exception):
    """Raised when the audio capture environment cannot be prepared."""


@dataclass
class AudioDeviceSummary:
    """Represents an audio device entry for diagnostics."""

    index: int
    name: str
    hostapi: str
    inputs: int
    outputs: int
    default_samplerate: Optional[float]


@dataclass
class AudioDiagnosticReport:
    """Aggregated view of the current audio environment."""

    platform: str
    mode: AudioCaptureMode
    input_devices: List[AudioDeviceSummary]
    loopback_candidates: List[AudioDeviceSummary]
    configured_device: Optional[AudioDeviceSummary]
    issues: List[str]
    recommendations: List[str]


class AudioEnvironmentManager:
    """Prepare platform-specific audio routing based on configuration."""

    def __init__(self, config: AudioInputConfig) -> None:
        self._config = config
        self._platform = platform.system().lower()
        self._repo_root = Path(__file__).resolve().parent.parent
        self._scripts_dir = self._repo_root / "scripts"
        self._cleanup_actions: List[Callable[[], None]] = []

    def prepare(self) -> None:
        """Ensure the selected capture mode has a viable device or routing."""

        mode = resolve_capture_mode(self._config)
        logging.debug("Preparing audio environment for mode=%s", mode.value)

        self._cleanup_actions.clear()

        self._ensure_device_presence()

        if mode is AudioCaptureMode.MICROPHONE:
            return

        if mode is AudioCaptureMode.API:
            logging.info(
                "Audio capture mode set to 'api'. Ensure external media ingestion is configured."
            )
            return

        if mode is AudioCaptureMode.LOOPBACK:
            self._prepare_loopback()
            return

    def cleanup(self) -> None:
        """Rollback any environment changes performed during prepare()."""

        while self._cleanup_actions:
            action = self._cleanup_actions.pop()
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                logging.warning("Audio environment cleanup failed: %s", exc)

    def _ensure_device_presence(self) -> None:
        try:
            devices = sd.query_devices()
        except Exception as exc:  # noqa: BLE001
            raise AudioEnvironmentError(f"Failed to enumerate audio devices: {exc}") from exc

        if not devices:
            raise AudioEnvironmentError("No audio devices detected by PortAudio.")

        if self._config.device_index is not None:
            index = self._config.device_index
            if index < 0 or index >= len(devices):
                raise AudioEnvironmentError(
                    f"Configured AUDIO_DEVICE_INDEX {index} is outside the available range (0-{len(devices)-1})."
                )
            device = devices[index]
            if device.get("max_input_channels", 0) <= 0:
                raise AudioEnvironmentError(
                    f"Configured device '{device.get('name', index)}' has no input channels."
                )
            logging.debug(
                "Audio device index %s resolved to '%s' (inputs=%s).",
                index,
                device.get("name", index),
                device.get("max_input_channels"),
            )
        else:
            if not any(dev.get("max_input_channels", 0) > 0 for dev in devices):
                raise AudioEnvironmentError("No input-capable audio devices detected.")

    def _prepare_loopback(self) -> None:
        if self._platform != "windows":
            raise AudioEnvironmentError("Loopback auto-setup currently supports Windows only.")
        self._prepare_windows_loopback()

    def _detect_loopback_candidate(self, keywords: Iterable[str]) -> bool:
        try:
            devices = sd.query_devices()
        except Exception as exc:  # noqa: BLE001
            raise AudioEnvironmentError(f"Failed to enumerate audio devices: {exc}") from exc

        keyword_set = {kw.lower() for kw in keywords}

        for index, device in enumerate(devices):
            inputs = device.get("max_input_channels", 0)
            if inputs <= 0:
                continue

            name = device.get("name", "").lower()
            is_candidate = any(keyword in name for keyword in keyword_set)

            if self._platform == "windows" and not is_candidate and "loopback" in name and "wasapi" in name:
                is_candidate = True

            if is_candidate:
                logging.debug(
                    "Detected loopback candidate #%s: %s (inputs=%s)",
                    index,
                    device.get("name", index),
                    inputs,
                )
                return True

        return False


def _hostapi_name(index: int) -> str:
    try:
        hostapis = sd.query_hostapis()
        if 0 <= index < len(hostapis):
            return hostapis[index].get("name", str(index))
    except Exception:  # noqa: BLE001
        return str(index)
    return str(index)


def _summarise_devices(devices: List[dict]) -> List[AudioDeviceSummary]:
    summaries: List[AudioDeviceSummary] = []
    for idx, device in enumerate(devices):
        summaries.append(
            AudioDeviceSummary(
                index=idx,
                name=device.get("name", f"Device {idx}"),
                hostapi=_hostapi_name(device.get("hostapi", -1)),
                inputs=device.get("max_input_channels", 0),
                outputs=device.get("max_output_channels", 0),
                default_samplerate=device.get("default_samplerate"),
            )
        )
    return summaries


def collect_audio_diagnostics(config: AudioInputConfig) -> AudioDiagnosticReport:
    """Gather cross-platform audio diagnostics for the current configuration."""

    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        raise AudioEnvironmentError(f"Failed to enumerate audio devices: {exc}") from exc

    input_devices = [dev for dev in devices if dev.get("max_input_channels", 0) > 0]
    summaries = _summarise_devices(devices)
    input_summaries = [summaries[idx] for idx, dev in enumerate(devices) if dev in input_devices]

    loopback_keywords = {"loopback", "stereo mix", "cable output", "cable input", "virtual"}
    platform_key = platform.system().lower()
    loopback_candidates: List[AudioDeviceSummary] = []
    keyword_set = {kw.lower() for kw in (loopback_keywords if platform_key == "windows" else set())}
    for summary in input_summaries:
        lowered = summary.name.lower()
        is_candidate = any(keyword in lowered for keyword in keyword_set)
        if platform_key == "windows" and not is_candidate and "loopback" in lowered and "wasapi" in lowered:
            is_candidate = True

        if is_candidate:
            loopback_candidates.append(summary)

    configured_device: Optional[AudioDeviceSummary] = None
    if config.device_index is not None:
        if 0 <= config.device_index < len(summaries):
            configured_device = summaries[config.device_index]

    effective_mode = resolve_capture_mode(config)

    issues: List[str] = []
    if not input_summaries:
        issues.append("入力デバイスが見つかりませんでした。サウンド設定を確認してください。")

    recommendations: List[str] = []
    if config.device_index is None and input_summaries:
        recommendations.append(
            "AUDIO_DEVICE_INDEX を設定するとデバイス切り替えの影響を受けにくくなります。"
        )
    if effective_mode is AudioCaptureMode.LOOPBACK and not loopback_candidates:
        if config.device_index is None:
            issues.append("ループバック入力候補が検出できませんでした。仮想デバイスやモニターを準備してください。")
        else:
            recommendations.append(
                "現在の AUDIO_DEVICE_INDEX がループバック経路を指しているかオーディオ設定で確認してください。"
            )
    if effective_mode is AudioCaptureMode.LOOPBACK and loopback_candidates:
        names = ", ".join(candidate.name for candidate in loopback_candidates[:3])
        recommendations.append(f"ループバック候補: {names}")

    return AudioDiagnosticReport(
        platform=platform.system(),
        mode=effective_mode,
        input_devices=input_summaries,
        loopback_candidates=loopback_candidates,
        configured_device=configured_device,
        issues=issues,
        recommendations=recommendations,
    )


def render_diagnostic_report(report: AudioDiagnosticReport) -> str:
    """Render a diagnostic report into a human-friendly multi-line string."""

    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("  オーディオ診断レポート")
    lines.append("=" * 60)
    lines.append(f"プラットフォーム: {report.platform}")
    lines.append(f"キャプチャモード: {report.mode.value}")
    lines.append("")

    lines.append("[入力デバイス一覧]")
    if not report.input_devices:
        lines.append("  (入力デバイスなし)")
    else:
        for device in report.input_devices:
            lines.append(
                f"  #{device.index:>3}: {device.name} | {device.inputs}ch in / {device.outputs}ch out | {device.hostapi}"
            )

    if report.configured_device:
        lines.append("")
        lines.append(
            f"設定済みデバイス: #{report.configured_device.index} {report.configured_device.name}"
        )

    lines.append("")
    lines.append("[ループバック候補]")
    if not report.loopback_candidates:
        lines.append("  (候補なし)")
    else:
        for candidate in report.loopback_candidates:
            lines.append(f"  #{candidate.index:>3}: {candidate.name}")

    if report.issues:
        lines.append("")
        lines.append("[課題]")
        for issue in report.issues:
            lines.append(f"  - {issue}")

    if report.recommendations:
        lines.append("")
        lines.append("[推奨事項]")
        for recommendation in report.recommendations:
            lines.append(f"  - {recommendation}")

    lines.append("")
    lines.append("詳細なルーティング手順は scripts/setup_audio_loopback_windows.ps1 を実行して確認してください。")
    return "\n".join(lines)


def run_cli_diagnostics(config: AudioInputConfig) -> None:
    """Execute diagnostics and print the rendered report."""

    report = collect_audio_diagnostics(config)
    output = render_diagnostic_report(report)
    print(output)


def resolve_capture_mode(config: AudioInputConfig) -> AudioCaptureMode:
    """Derive the effective capture mode given configuration and platform defaults."""

    if config.mode is AudioCaptureMode.AUTO:
        system = platform.system().lower()
        if system == "windows":
            return AudioCaptureMode.LOOPBACK
        return AudioCaptureMode.MICROPHONE
    return config.mode
