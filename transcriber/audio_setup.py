"""Helpers for preparing and diagnosing audio capture environments."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

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
    output_devices: List[AudioDeviceSummary]
    loopback_candidates: List[AudioDeviceSummary]
    configured_device: Optional[AudioDeviceSummary]
    default_input_device: Optional[AudioDeviceSummary]
    default_output_device: Optional[AudioDeviceSummary]
    issues: List[str]
    recommendations: List[str]


def _sanitize_restore_defaults(
    defaults: Optional[tuple[Optional[str], Optional[str]]],
) -> Optional[tuple[Optional[str], Optional[str]]]:
    """Drop leftover virtual-sink values: restoring them would re-pin the
    codex_transcribe routing after an unclean previous exit."""

    if not defaults:
        return None
    sink, source = defaults
    if sink and sink.startswith("codex_transcribe"):
        sink = None
    if source and source.startswith("codex_transcribe"):
        source = None
    if sink or source:
        return (sink, source)
    return None


class AudioEnvironmentManager:
    """Prepare platform-specific audio routing based on configuration."""

    def __init__(self, config: AudioInputConfig) -> None:
        self._config = config
        self._platform = platform.system().lower()
        self._repo_root = Path(__file__).resolve().parent.parent
        self._scripts_dir = self._repo_root / "scripts"
        self._cleanup_actions: List[Callable[[], None]] = []
        self._expected_source: Optional[str] = None
        self._enforce_failures = 0
        # Serializes enforce_default_source (which may run on an executor
        # thread) with cleanup(), so a late enforcement can never re-pin the
        # monitor after the exit-time restore already ran.
        self._enforce_lock = threading.Lock()

    def prepare(self) -> None:
        """Ensure the selected capture mode has a viable device or routing."""

        mode = resolve_capture_mode(self._config)
        logging.debug("Preparing audio environment for mode=%s", mode.value)

        self._cleanup_actions.clear()

        try:
            self._ensure_device_presence()

            if mode is AudioCaptureMode.API:
                logging.info(
                    "Audio capture mode set to 'api'. Ensure external media ingestion is configured."
                )
            elif mode is AudioCaptureMode.LOOPBACK:
                self._prepare_loopback()
        except Exception:
            # Roll back routing changes made before the failure; otherwise the
            # system stays half-configured (e.g. default sink already switched
            # to the virtual sink) with no one left to restore it.
            self.cleanup()
            raise

    def cleanup(self) -> None:
        """Rollback any environment changes performed during prepare()."""

        with self._enforce_lock:
            # Disarm enforcement first so an in-flight or later watchdog tick
            # cannot undo the restore below.
            self._expected_source = None
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
        if self._platform == "linux":
            self._prepare_linux_loopback()
        elif self._platform == "windows":
            self._prepare_windows_loopback()
        elif self._platform == "darwin":
            self._prepare_macos_loopback()
        else:
            logging.warning(
                "Loopback capture not explicitly supported on platform '%s'. Ensure routing manually.",
                self._platform,
            )

    def _prepare_linux_loopback(self) -> None:
        if not shutil.which("pactl"):
            logging.warning(
                "pactl not found; cannot auto-configure PipeWire/PulseAudio loopback. "
                "Ensure a monitor source is selected manually."
            )
            return

        capture_defaults: Optional[tuple[Optional[str], Optional[str]]] = None
        if os.environ.get("AUDIO_LOOPBACK_ALREADY_SET") == "1":
            logging.debug("Loopback already set by launcher; verifying availability only.")
            if self._config.device_index is None:
                defaults = self._get_linux_defaults()
                source = defaults[1] if defaults else None
                if source:
                    # PortAudio cannot tell monitors apart, but pactl can:
                    # require that the default source really is a monitor.
                    if not source.endswith(".monitor"):
                        raise AudioEnvironmentError(
                            "AUDIO_LOOPBACK_ALREADY_SET=1 but the default source "
                            f"({source}) is not a monitor. Run "
                            "scripts/setup_audio_loopback_linux.sh or unset the flag."
                        )
                    self._expected_source = source
                elif not self._detect_loopback_candidate({"monitor", "loopback"}):
                    raise AudioEnvironmentError(
                        "Loopback auto-setup flag set but no monitor source detected."
                    )
            return

        if self._config.auto_setup_loopback:
            raw_defaults = self._get_linux_defaults()
            capture_defaults = _sanitize_restore_defaults(raw_defaults)
            if capture_defaults:
                self._register_linux_defaults_restore(capture_defaults)
            if raw_defaults is not None:
                # Any field whose captured value was a codex_transcribe
                # leftover (unclean earlier exit) has no usable snapshot;
                # restore that field to the first physical device on exit
                # instead of leaving it pinned to the virtual routing. This
                # also covers mixed states, e.g. GNOME re-picked the speakers
                # but the default source stayed on the monitor.
                good_sink = capture_defaults[0] if capture_defaults else None
                good_source = capture_defaults[1] if capture_defaults else None
                need_sink = raw_defaults[0] is not None and good_sink is None
                need_source = raw_defaults[1] is not None and good_source is None
                if need_sink or need_source:
                    logging.warning(
                        "Previous session left the virtual sink as a default "
                        "(sink=%s, source=%s); will restore physical devices on exit.",
                        raw_defaults[0],
                        raw_defaults[1],
                    )
                    self._cleanup_actions.append(
                        lambda ns=need_sink, nsrc=need_source: self._restore_physical_defaults(
                            restore_sink=ns, restore_source=nsrc
                        )
                    )

            script = self._scripts_dir / "setup_audio_loopback_linux.sh"
            if script.is_file():
                env = os.environ.copy()
                if self._config.linux_loopback_sink:
                    env["HEADPHONE_SINK"] = self._config.linux_loopback_sink
                try:
                    subprocess.run(["bash", str(script)], check=True, env=env)
                except subprocess.CalledProcessError as exc:
                    raise AudioEnvironmentError(
                        "Failed to initialise PipeWire virtual loopback (setup_audio_loopback_linux.sh)."
                    ) from exc
                # Arm drift enforcement only when capture follows the system
                # default; a pinned AUDIO_DEVICE_INDEX means deliberate input
                # changes must not be fought.
                if self._config.device_index is None:
                    after = self._get_linux_defaults()
                    if after and after[1] and after[1].endswith(".monitor"):
                        self._expected_source = after[1]
            else:
                logging.debug(
                    "Loopback helper script not found at %s; skipping auto-setup.", script
                )
        if not self._detect_loopback_candidate({"monitor", "loopback"}):
            if self._config.device_index is None:
                raise AudioEnvironmentError(
                    "No loopback-capable input detected after setup. "
                    "Verify that a monitor source is available and not muted."
                )
            logging.debug(
                "Loopback candidates not found, but AUDIO_DEVICE_INDEX=%s is configured; continuing.",
                self._config.device_index,
            )

    def _get_linux_defaults(self) -> Optional[tuple[Optional[str], Optional[str]]]:
        try:
            output = subprocess.check_output(
                ["pactl", "info"],
                text=True,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "LC_ALL": "C"},
                timeout=5,
            )
        except Exception:  # noqa: BLE001
            return None

        sink = None
        source = None
        for line in output.splitlines():
            if line.startswith("Default Sink:"):
                sink = line.split(":", 1)[1].strip() or None
            elif line.startswith("Default Source:"):
                source = line.split(":", 1)[1].strip() or None
        if sink or source:
            return (sink, source)
        return None

    def _register_linux_defaults_restore(
        self, defaults: tuple[Optional[str], Optional[str]]
    ) -> None:
        sink, source = defaults

        def restore(sink_name: Optional[str] = sink, source_name: Optional[str] = source) -> None:
            if sink_name:
                subprocess.run(["pactl", "set-default-sink", sink_name], check=False)
            if source_name:
                subprocess.run(["pactl", "set-default-source", source_name], check=False)

        self._cleanup_actions.append(restore)

    @staticmethod
    def _list_pactl_names(kind: str) -> List[str]:
        try:
            output = subprocess.check_output(
                ["pactl", "list", "short", kind],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:  # noqa: BLE001
            return []
        names: List[str] = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].strip():
                names.append(parts[1].strip())
        return names

    def _restore_physical_defaults(
        self, restore_sink: bool = True, restore_source: bool = True
    ) -> None:
        """Point defaults at the first physical devices for the requested
        fields (used when the pre-session snapshot for that field was a
        codex_transcribe leftover from an earlier unclean exit)."""

        sink_choice: Optional[str] = None
        source_choice: Optional[str] = None
        if restore_sink:
            sinks = [
                n for n in self._list_pactl_names("sinks") if not n.startswith("codex_transcribe")
            ]
            sinks.sort(key=lambda n: (0 if n.startswith("alsa_output") else 1))
            if sinks:
                sink_choice = sinks[0]
                subprocess.run(["pactl", "set-default-sink", sink_choice], check=False)
        if restore_source:
            sources = [
                n
                for n in self._list_pactl_names("sources")
                if not n.startswith("codex_transcribe") and not n.endswith(".monitor")
            ]
            sources.sort(key=lambda n: (0 if n.startswith("alsa_input") else 1))
            if sources:
                source_choice = sources[0]
                subprocess.run(["pactl", "set-default-source", source_choice], check=False)
        logging.info(
            "Restored physical audio defaults (sink=%s, source=%s).",
            sink_choice or "unchanged",
            source_choice or "unchanged",
        )

    def enforce_default_source(self) -> bool:
        """Re-pin the default source to the session's monitor if something
        (Bluetooth connect, GNOME sound settings, WirePlumber) moved it.

        Returns True when a drift was corrected. Safe to call periodically;
        serialized against cleanup() so it can never race the exit restore.
        """

        if self._platform != "linux" or not self._expected_source:
            return False
        with self._enforce_lock:
            expected = self._expected_source
            if not expected:
                # cleanup() disarmed enforcement while we waited for the lock.
                return False
            try:
                defaults = self._get_linux_defaults()
                current = defaults[1] if defaults else None
                if current == expected:
                    self._enforce_failures = 0
                    return False
                if expected not in self._list_pactl_names("sources"):
                    # The monitor itself is gone (modules unloaded?); nothing
                    # safe to enforce.
                    return False
                logging.warning(
                    "Default input drifted to %s during the session; re-pinning %s "
                    "so meeting audio keeps flowing.",
                    current,
                    expected,
                )
                subprocess.run(
                    ["pactl", "set-default-source", expected], check=False, timeout=5
                )
                self._enforce_failures = 0
                return True
            except Exception as exc:  # noqa: BLE001
                self._enforce_failures += 1
                if self._enforce_failures <= 3:
                    logging.debug("Default-source enforcement check failed: %s", exc)
                return False

    def _prepare_windows_loopback(self) -> None:
        if self._config.auto_setup_loopback:
            script = self._scripts_dir / "setup_audio_loopback_windows.ps1"
            powershell = shutil.which("powershell")
            if script.is_file() and powershell:
                try:
                    subprocess.run(
                        [
                            powershell,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(script),
                        ],
                        check=True,
                    )
                except subprocess.CalledProcessError as exc:
                    logging.warning(
                        "Windows loopback helper exited with code %s. Continuing with verification.",
                        exc.returncode,
                    )
            else:
                logging.debug(
                    "Skipping Windows loopback helper (script=%s, powershell=%s).",
                    script.exists(),
                    bool(powershell),
                )

        if self._config.device_index is None and not self._detect_loopback_candidate(
            {"loopback", "stereo mix", "cable output", "cable input", "virtual"}
        ):
            raise AudioEnvironmentError(
                "No Windows loopback-capable input detected. "
                "Configure 'Stereo Mix', WASAPI loopback, or a virtual cable."
            )

    def _prepare_macos_loopback(self) -> None:
        if self._config.auto_setup_loopback:
            script = self._scripts_dir / "setup_audio_loopback_macos.sh"
            if script.is_file():
                try:
                    subprocess.run(["bash", str(script)], check=True)
                except subprocess.CalledProcessError as exc:
                    logging.warning(
                        "macOS loopback helper exited with code %s. Continuing with verification.",
                        exc.returncode,
                    )
            else:
                logging.debug("macOS loopback helper script not found at %s.", script)

        if self._config.device_index is None and not self._detect_loopback_candidate(
            {"blackhole", "loopback", "soundflower", "aggregate", "multi-output"}
        ):
            raise AudioEnvironmentError(
                "No macOS loopback device detected. Install BlackHole or create an aggregate device."
            )

    def _detect_loopback_candidate(self, keywords: Iterable[str]) -> bool:
        try:
            devices = sd.query_devices()
        except Exception as exc:  # noqa: BLE001
            raise AudioEnvironmentError(f"Failed to enumerate audio devices: {exc}") from exc

        keyword_set = {kw.lower() for kw in keywords}
        sink_hint = (
            self._config.linux_loopback_sink.lower()
            if self._platform == "linux" and self._config.linux_loopback_sink
            else None
        )

        for index, device in enumerate(devices):
            inputs = device.get("max_input_channels", 0)
            if inputs <= 0:
                continue

            name = device.get("name", "").lower()
            is_candidate = any(keyword in name for keyword in keyword_set)

            if self._platform == "linux":
                if not is_candidate and sink_hint and sink_hint in name:
                    is_candidate = True
                if not is_candidate and name in {"pipewire", "default"} and inputs >= 2:
                    is_candidate = True
            elif self._platform == "windows":
                if not is_candidate and "loopback" in name and "wasapi" in name:
                    is_candidate = True
            elif self._platform == "darwin":
                if not is_candidate and "blackhole" in name:
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


def _looks_like_loopback_device(name: str) -> bool:
    lowered = name.lower()
    keywords = (
        "monitor",
        "loopback",
        "stereo mix",
        "ステレオ",
        "ミキサー",
        "cable output",
        "cable input",
        "vb-audio",
        "virtual",
        "blackhole",
        "soundflower",
        "仮想",
        "codex_transcribe",
    )
    return any(keyword in lowered for keyword in keywords)


def _device_name_matches(preferred_name: str, actual_name: str) -> bool:
    preferred = " ".join(preferred_name.lower().split())
    actual = " ".join(actual_name.lower().split())
    if not preferred or not actual:
        return False
    return preferred == actual or preferred in actual or actual in preferred


def _find_input_device_by_name(
    devices: Iterable[AudioDeviceSummary],
    preferred_name: Optional[str],
) -> Optional[AudioDeviceSummary]:
    if not preferred_name:
        return None

    candidates: List[Tuple[int, int, int, int, AudioDeviceSummary]] = []
    for device in devices:
        if device.inputs <= 0 or not _device_name_matches(preferred_name, device.name):
            continue
        lowered = device.name.lower()
        hostapi = device.hostapi.lower()
        candidates.append(
            (
                0 if device.name.lower() == preferred_name.lower() else 1,
                0 if any(api in hostapi for api in ("pipewire", "pulse", "wasapi", "core audio")) else 1,
                0 if any(term in lowered for term in ("monitor", "loopback", "cable output", "blackhole")) else 1,
                device.index,
                device,
            )
        )

    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[:4])[0][4]


def _looks_like_physical_output_device(name: str) -> bool:
    lowered = name.lower()
    loopback_send_terms = (
        "cable input",
        "cable in",
        "vb-audio",
        "virtual",
        "blackhole",
        "soundflower",
        "codex_transcribe",
        "仮想",
    )
    if any(term in lowered for term in loopback_send_terms):
        return False
    physical_terms = (
        "speaker",
        "speakers",
        "headphone",
        "headphones",
        "headset",
        "realtek",
        "hdmi",
        "displayport",
        "スピーカー",
        "ヘッドホン",
        "ヘッドフォン",
        "ヘッドセット",
    )
    return any(term in lowered for term in physical_terms)


def _default_device_summary(kind: str, summaries: List[AudioDeviceSummary]) -> Optional[AudioDeviceSummary]:
    try:
        device = sd.query_devices(kind=kind)
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(device, dict):
        return None

    index = device.get("index")
    if isinstance(index, int) and 0 <= index < len(summaries):
        return summaries[index]

    name = device.get("name")
    if not name:
        return None
    for summary in summaries:
        if summary.name == name:
            return summary
    return None


def collect_audio_diagnostics(config: AudioInputConfig) -> AudioDiagnosticReport:
    """Gather cross-platform audio diagnostics for the current configuration."""

    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        raise AudioEnvironmentError(f"Failed to enumerate audio devices: {exc}") from exc

    summaries = _summarise_devices(devices)
    input_summaries = [
        summaries[idx] for idx, dev in enumerate(devices) if dev.get("max_input_channels", 0) > 0
    ]
    output_summaries = [
        summaries[idx] for idx, dev in enumerate(devices) if dev.get("max_output_channels", 0) > 0
    ]

    loopback_keywords = {
        "linux": {"monitor", "loopback"},
        "windows": {"loopback", "stereo mix", "cable output", "cable input", "virtual"},
        "darwin": {"blackhole", "loopback", "soundflower", "aggregate", "multi-output"},
    }
    platform_key = platform.system().lower()
    loopback_candidates: List[AudioDeviceSummary] = []
    keywords = loopback_keywords.get(platform_key, set())
    keyword_set = {kw.lower() for kw in keywords}
    sink_hint = (
        config.linux_loopback_sink.lower()
        if platform_key == "linux" and config.linux_loopback_sink
        else None
    )
    for summary in input_summaries:
        lowered = summary.name.lower()
        is_candidate = any(keyword in lowered for keyword in keyword_set)
        if platform_key == "linux":
            if not is_candidate and sink_hint and sink_hint in lowered:
                is_candidate = True
            if not is_candidate and lowered in {"pipewire", "default"} and summary.inputs >= 2:
                is_candidate = True
        elif platform_key == "windows":
            if not is_candidate and "loopback" in lowered and "wasapi" in lowered:
                is_candidate = True
        elif platform_key == "darwin":
            if not is_candidate and "blackhole" in lowered:
                is_candidate = True

        if is_candidate:
            loopback_candidates.append(summary)

    preferred_named_device = _find_input_device_by_name(
        input_summaries,
        config.windows_loopback_device or config.mac_loopback_device or config.linux_loopback_sink,
    )
    configured_device: Optional[AudioDeviceSummary] = None
    if config.device_index is not None:
        if 0 <= config.device_index < len(summaries):
            configured_device = summaries[config.device_index]
        else:
            if preferred_named_device:
                recommendations_hint = (
                    f"AUDIO_DEVICE_INDEX={config.device_index} は現在のデバイス範囲外ですが、"
                    f"優先デバイス名は #{preferred_named_device.index} {preferred_named_device.name} に一致しています。"
                    " 起動時は名前で拾い直します。"
                )
            else:
                recommendations_hint = ""
            configured_device = None

    default_input_device = _default_device_summary("input", summaries)
    default_output_device = _default_device_summary("output", summaries)

    effective_mode = resolve_capture_mode(config)

    issues: List[str] = []
    recommendations: List[str] = []
    if config.device_index is not None and not (0 <= config.device_index < len(summaries)):
        if preferred_named_device:
            recommendations.append(recommendations_hint)
        else:
            issues.append(
                f"AUDIO_DEVICE_INDEX={config.device_index} は現在のデバイス範囲外です。"
                "USB/BT機器の抜き差しで番号が変わった可能性があります。"
            )
    if not input_summaries:
        issues.append("入力デバイスが見つかりませんでした。サウンド設定を確認してください。")

    if config.device_index is None and input_summaries:
        recommendations.append(
            "AUDIO_DEVICE_INDEX を設定するとデバイス切り替えの影響を受けにくくなります。"
        )
    if preferred_named_device:
        if config.device_index is None or config.device_index != preferred_named_device.index:
            recommendations.append(
                f"優先デバイス名は #{preferred_named_device.index} {preferred_named_device.name} に一致しています。"
                " 番号が変わっても起動時にこの名前を優先します。"
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
    if configured_device:
        if configured_device.inputs <= 0:
            issues.append(
                "AUDIO_DEVICE_INDEX は再生専用デバイスを指しているため録音できません。"
                " `--audio-profile microphone` または `--audio-profile loopback` で現在の入力候補を確認してください。"
            )
        configured_is_loopback = _looks_like_loopback_device(configured_device.name) or any(
            candidate.index == configured_device.index for candidate in loopback_candidates
        )
        if (
            preferred_named_device
            and configured_device.index != preferred_named_device.index
            and not _device_name_matches(preferred_named_device.name, configured_device.name)
        ):
            recommendations.append(
                f"AUDIO_DEVICE_INDEX は #{configured_device.index} {configured_device.name} を指していますが、"
                f"優先デバイス名は #{preferred_named_device.index} {preferred_named_device.name} に一致しています。"
                " 起動時は名前で見つかったデバイスを優先します。"
            )
        if effective_mode is AudioCaptureMode.MICROPHONE and configured_is_loopback:
            issues.append(
                "キャプチャモードは microphone ですが、AUDIO_DEVICE_INDEX はループバック系デバイスを指しているようです。"
            )
        elif effective_mode is AudioCaptureMode.LOOPBACK and not configured_is_loopback:
            recommendations.append(
                "キャプチャモードは loopback ですが、設定済みデバイス名がループバック候補らしくありません。番号を再確認してください。"
            )
    if default_output_device and _looks_like_loopback_device(default_output_device.name):
        physical_outputs = [
            output.name for output in output_summaries if _looks_like_physical_output_device(output.name)
        ]
        recommendations.append(
            "既定の再生デバイスが仮想/ループバック系です。文字起こし対象外のアプリは物理スピーカー/ヘッドホンへ戻し、"
            "対象アプリだけを仮想sinkや仮想ケーブルへ流すと混乱が少ないです。"
        )
        if physical_outputs:
            recommendations.append("物理出力候補: " + ", ".join(physical_outputs[:3]))

    return AudioDiagnosticReport(
        platform=platform.system(),
        mode=effective_mode,
        input_devices=input_summaries,
        output_devices=output_summaries,
        loopback_candidates=loopback_candidates,
        configured_device=configured_device,
        default_input_device=default_input_device,
        default_output_device=default_output_device,
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

    lines.append("[既定デバイス]")
    if report.default_output_device:
        lines.append(
            f"  再生: #{report.default_output_device.index} {report.default_output_device.name}"
        )
    else:
        lines.append("  再生: (取得できませんでした)")
    if report.default_input_device:
        lines.append(
            f"  録音: #{report.default_input_device.index} {report.default_input_device.name}"
        )
    else:
        lines.append("  録音: (取得できませんでした)")
    lines.append("")

    lines.append("[出力デバイス一覧]")
    if not report.output_devices:
        lines.append("  (出力デバイスなし)")
    else:
        for device in report.output_devices:
            lines.append(
                f"  #{device.index:>3}: {device.name} | {device.inputs}ch in / {device.outputs}ch out | {device.hostapi}"
            )
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
    lines.append("詳細なルーティング手順は docs/audio_loopback.md を参照してください。")
    return "\n".join(lines)


def _format_device_ref(device: Optional[AudioDeviceSummary]) -> str:
    if device is None:
        return "(未検出)"
    return f"#{device.index} {device.name}"


def _unique_names(devices: Iterable[AudioDeviceSummary]) -> List[str]:
    names: List[str] = []
    seen = set()
    for device in devices:
        if device.name in seen:
            continue
        seen.add(device.name)
        names.append(device.name)
    return names


def render_routing_guide(report: AudioDiagnosticReport) -> str:
    """Render a concise operating guide for the current routing state."""

    physical_outputs = _unique_names(
        device for device in report.output_devices if _looks_like_physical_output_device(device.name)
    )
    loopback_input = next(
        (
            device
            for device in report.loopback_candidates
            if any(term in device.name.lower() for term in ("monitor", "loopback", "cable output", "blackhole"))
        ),
        report.loopback_candidates[0] if report.loopback_candidates else None,
    )

    lines: List[str] = []
    lines.append("====================")
    lines.append("音声ルーティングガイド")
    lines.append("====================")
    lines.append(f"現在の .env モード: {report.mode.value}")
    lines.append(f"現在の入力: {_format_device_ref(report.configured_device)}")
    lines.append(f"既定再生: {_format_device_ref(report.default_output_device)}")
    lines.append(f"既定録音: {_format_device_ref(report.default_input_device)}")
    lines.append("")

    if report.platform.lower() == "linux":
        lines.append("[Linuxループバックの全体像]")
        lines.append("  Edge / Chrome / Discord など")
        lines.append("    出力先: codex_transcribe 仮想sink")
        lines.append("          |")
        lines.append("          v")
        lines.append("    [PipeWire/PulseAudio module-null-sink]")
        lines.append("          |")
        lines.append("          +--> codex_transcribe.monitor")
        lines.append("          |      +--> このツールが入力として拾う")
        lines.append("          |            -> Speechmatics / 翻訳 / Web UI / ログ")
        lines.append("          |")
        lines.append("          +--> module-loopback")
        lines.append("                 再生先: 元のヘッドホン / スピーカー sink")
        lines.append("                 -> 自分の耳で聞く")
        lines.append("")
        lines.append(
            "  AUDIO_LINUX_LOOPBACK_SINK には、録音元ではなく戻し先の物理sink名を指定します。"
        )
        lines.append("  このツールの録音元は、その sink の monitor または pipewire/default 入力候補です。")
        lines.append("")

    lines.append("[マイクだけ文字起こし]")
    lines.append("  1. python -m transcriber.cli --apply-audio-profile microphone")
    lines.append("  2. python -m transcriber.cli --test-audio-levels 3 --test-audio-profile microphone")
    lines.append("  3. 通常のアプリ出力は既定または物理スピーカー/ヘッドホンへ戻します。")
    if physical_outputs:
        lines.append(f"  物理出力候補: {', '.join(physical_outputs[:3])}")
    lines.append("")

    lines.append("[PC音声を文字起こし]")
    if report.platform.lower() == "linux":
        lines.append("  1. 必要なら scripts/setup_audio_loopback_linux.sh で仮想sink/monitorを準備します。")
    else:
        lines.append("  1. OS側で仮想ケーブル/monitor/BlackHoleなどのループバック入力を準備します。")
    lines.append("  2. python -m transcriber.cli --apply-audio-profile loopback")
    if loopback_input:
        lines.append(f"  3. このツールの入力候補: {_format_device_ref(loopback_input)}")
    else:
        lines.append("  3. PipeWire/PulseAudio monitor、VB-CABLE、BlackHoleなどの入力を準備します。")
    lines.append("  4. python -m transcriber.cli --test-audio-levels 3 --test-audio-profile loopback")
    lines.append("  5. 聞きながら使う場合は、対象音声の監視先を現在使うスピーカー/ヘッドホンにします。")
    lines.append("")

    lines.append("[元に戻す]")
    lines.append("  - .env を戻す: python -m transcriber.cli --restore-env-backup latest")
    lines.append("  - マイク運用へ戻す: python -m transcriber.cli --apply-audio-profile microphone")
    lines.append("  - Speechmatics を標準精度へ戻す: python -m transcriber.cli --set-speechmatics-operating-point standard")
    lines.append("  - Linuxの仮想sinkを整理する場合は scripts/reset_audio_defaults.sh を使います。")
    lines.append("")

    if report.default_output_device and _looks_like_loopback_device(report.default_output_device.name):
        lines.append("[注意]")
        lines.append(
            "  既定再生が仮想/ループバック系です。文字起こし対象外のアプリは物理出力へ戻してください。"
        )
        lines.append("")

    lines.append("このガイドは設定を変更しません。")
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
        if system in {"linux", "windows", "darwin"}:
            return AudioCaptureMode.LOOPBACK
        return AudioCaptureMode.MICROPHONE
    return config.mode
