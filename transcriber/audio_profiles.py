"""Safe audio and Speechmatics profile helpers."""

from __future__ import annotations

import platform
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class DeviceChoice:
    index: int
    name: str
    hostapi: str
    inputs: int
    outputs: int
    default_samplerate: Optional[float]


@dataclass(frozen=True)
class ProfilePlan:
    profile: str
    device: Optional[DeviceChoice]
    updates: Dict[str, str]
    notes: List[str]
    warnings: List[str]


_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\s*)$")


def _load_sounddevice() -> Any:
    import sounddevice as sd

    return sd


def _hostapi_name(index: int, hostapis: Optional[List[dict]] = None) -> str:
    try:
        if hostapis is None:
            hostapis = _load_sounddevice().query_hostapis()
        if 0 <= index < len(hostapis):
            return str(hostapis[index].get("name", index))
    except Exception:  # noqa: BLE001
        return str(index)
    return str(index)


def _device_choices() -> List[DeviceChoice]:
    sd = _load_sounddevice()
    devices = sd.query_devices()
    try:
        hostapis = sd.query_hostapis()
    except Exception:  # noqa: BLE001
        hostapis = None

    choices: List[DeviceChoice] = []
    for index, device in enumerate(devices):
        choices.append(
            DeviceChoice(
                index=index,
                name=str(device.get("name", f"Device {index}")),
                hostapi=_hostapi_name(int(device.get("hostapi", -1)), hostapis),
                inputs=int(device.get("max_input_channels", 0)),
                outputs=int(device.get("max_output_channels", 0)),
                default_samplerate=device.get("default_samplerate"),
            )
        )
    return choices


def _looks_like_loopback(name: str) -> bool:
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
    )
    return any(keyword in lowered for keyword in keywords)


def _looks_like_microphone(name: str) -> bool:
    lowered = name.lower()
    keywords = ("microphone", "mic", "マイク")
    return any(keyword in lowered for keyword in keywords) and not _looks_like_loopback(name)


def _rate_or_default(device: DeviceChoice, default: int = 16000) -> int:
    if device.default_samplerate:
        return int(round(device.default_samplerate))
    return default


def _input_devices(devices: Optional[Iterable[DeviceChoice]]) -> Iterable[DeviceChoice]:
    return devices if devices is not None else _device_choices()


def choose_microphone(devices: Optional[Iterable[DeviceChoice]] = None) -> Optional[DeviceChoice]:
    candidates = [d for d in _input_devices(devices) if d.inputs > 0 and not _looks_like_loopback(d.name)]
    if not candidates:
        return None

    def score(device: DeviceChoice) -> Tuple[int, int, int]:
        hostapi = device.hostapi.lower()
        return (
            0 if any(name in hostapi for name in ("pipewire", "pulse", "wasapi", "core audio")) else 1,
            0 if _looks_like_microphone(device.name) else 1,
            device.index,
        )

    return sorted(candidates, key=score)[0]


def choose_loopback(devices: Optional[Iterable[DeviceChoice]] = None) -> Optional[DeviceChoice]:
    candidates = [
        d
        for d in _input_devices(devices)
        if d.inputs > 0 and (_looks_like_loopback(d.name) or d.name.lower() in {"default", "pipewire", "pulse"})
    ]
    if not candidates:
        return None

    def score(device: DeviceChoice) -> Tuple[int, int, int, int, int]:
        name = device.name.lower()
        hostapi = device.hostapi.lower()
        return (
            0 if any(api in hostapi for api in ("pipewire", "pulse", "wasapi", "core audio")) else 1,
            0 if any(term in name for term in ("monitor", "loopback", "cable output", "blackhole")) else 1,
            0 if name not in {"default", "pipewire", "pulse"} else 1,
            0 if device.default_samplerate and int(round(device.default_samplerate)) == 16000 else 1,
            device.index,
        )

    return sorted(candidates, key=score)[0]


def build_profile_plan(profile: str) -> ProfilePlan:
    profile = profile.strip().lower()
    devices = _device_choices()
    system = platform.system().lower()
    notes: List[str] = []
    warnings: List[str] = []

    if profile == "microphone":
        device = choose_microphone(devices)
        if device is None:
            return ProfilePlan(profile, None, {}, [], ["入力可能なマイク候補が見つかりませんでした。"])
        updates = {
            "AUDIO_CAPTURE_MODE": "microphone",
            "AUDIO_DEVICE_INDEX": str(device.index),
            "AUDIO_DEVICE_SAMPLE_RATE": str(_rate_or_default(device)),
            "AUDIO_SAMPLE_RATE": "16000",
            "AUDIO_CHANNELS": "1",
            "AUDIO_AUTO_SETUP_LOOPBACK": "false",
            "AUDIO_WINDOWS_LOOPBACK_DEVICE": "",
            "AUDIO_MAC_LOOPBACK_DEVICE": "",
        }
        notes.append("PC音は拾わず、選択したマイク入力だけを Speechmatics へ送る構成です。")
        if system == "linux":
            notes.append("アプリ音声を拾いたい場合は loopback プロファイルか PipeWire/PulseAudio の monitor source を使います。")
        return ProfilePlan(profile, device, updates, notes, warnings)

    if profile == "loopback":
        device = choose_loopback(devices)
        if device is None:
            return ProfilePlan(
                profile,
                None,
                {},
                [],
                ["PipeWire/PulseAudio monitor、VB-CABLE、BlackHole などのループバック入力候補が見つかりませんでした。"],
            )
        updates = {
            "AUDIO_CAPTURE_MODE": "loopback",
            "AUDIO_DEVICE_INDEX": str(device.index),
            "AUDIO_DEVICE_SAMPLE_RATE": str(_rate_or_default(device)),
            "AUDIO_SAMPLE_RATE": "16000",
            "AUDIO_CHANNELS": "1",
            "AUDIO_AUTO_SETUP_LOOPBACK": "true",
        }
        if system == "windows":
            updates["AUDIO_WINDOWS_LOOPBACK_DEVICE"] = device.name
        elif system == "darwin":
            updates["AUDIO_MAC_LOOPBACK_DEVICE"] = device.name
        notes.append("PC出力音声を Speechmatics へ送る構成です。")
        if system == "linux":
            notes.append("必要なら `scripts/setup_audio_loopback_linux.sh` で仮想sinkとmonitor sourceを準備してください。")
            notes.append("対象アプリを仮想sinkへ流し、聞く先は物理スピーカー/ヘッドホンへ戻すと混乱が少ないです。")
        elif system == "windows":
            notes.append("対象アプリの出力先を CABLE Input にし、このツールは CABLE Output からPC音を拾います。")
        elif system == "darwin":
            notes.append("BlackHoleやMulti-Output Deviceのルーティングを確認してください。")
        return ProfilePlan(profile, device, updates, notes, warnings)

    raise ValueError(f"Unsupported audio profile: {profile}")


def _unique_env_sidecar_path(env_path: Path, marker: str) -> Path:
    """Return a timestamped path without overwriting an existing backup."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{env_path.name}.{marker}.{timestamp}"
    candidate = env_path.with_name(base_name)
    counter = 1
    while candidate.exists():
        candidate = env_path.with_name(f"{base_name}.{counter:02d}")
        counter += 1
    return candidate


def apply_env_updates_to_text(text: str, updates: Dict[str, str]) -> str:
    lines = text.splitlines()
    remaining = dict(updates)
    result: List[str] = []

    for line in lines:
        match = _KEY_RE.match(line)
        if match and not line.lstrip().startswith("#"):
            prefix, key, separator, _old_value, suffix = match.groups()
            if key in remaining:
                result.append(f"{prefix}{key}{separator}{remaining.pop(key)}{suffix}")
                continue
        result.append(line)

    if remaining:
        if result and result[-1].strip():
            result.append("")
        result.append("# Profile generated by transcriber.cli")
        for key, value in remaining.items():
            result.append(f"{key}={value}")

    return "\n".join(result) + "\n"


def _active_env_value(text: str, key_name: str) -> Optional[str]:
    """Return the active value for a key in .env text, ignoring comments."""

    for line in text.splitlines():
        match = _KEY_RE.match(line)
        if not match or line.lstrip().startswith("#"):
            continue
        _prefix, key, _separator, value, _suffix = match.groups()
        if key == key_name:
            return value.strip().strip('"').strip("'")
    return None


def _env_already_has_updates(text: str, updates: Dict[str, str]) -> bool:
    for key, expected_value in updates.items():
        current_value = _active_env_value(text, key)
        if current_value != expected_value:
            return False
    return True


def apply_env_updates_to_file(updates: Dict[str, str], env_path: Path = Path(".env")) -> Path:
    """Create a sidecar .env backup, apply key/value updates, and return the backup path."""

    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} does not exist. Copy .env.example to .env first.")

    backup_path = _unique_env_sidecar_path(env_path, "bak")
    shutil.copy2(env_path, backup_path)

    original = env_path.read_text(encoding="utf-8")
    updated = apply_env_updates_to_text(original, updates)
    env_path.write_text(updated, encoding="utf-8")
    return backup_path


def apply_profile_to_env(profile: str, env_path: Path = Path(".env")) -> Tuple[ProfilePlan, Optional[Path]]:
    plan = build_profile_plan(profile)
    if plan.device is None:
        raise RuntimeError("; ".join(plan.warnings) or "No suitable audio device found.")

    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} does not exist. Copy .env.example to .env first.")

    original = env_path.read_text(encoding="utf-8")
    if _env_already_has_updates(original, plan.updates):
        return plan, None

    backup_path = apply_env_updates_to_file(plan.updates, env_path=env_path)
    return plan, backup_path


def apply_speechmatics_operating_point(
    operating_point: str,
    env_path: Path = Path(".env"),
) -> Optional[Path]:
    """Safely switch Speechmatics realtime accuracy mode in .env."""

    value = operating_point.strip().lower()
    if value not in {"standard", "enhanced"}:
        raise ValueError("operating_point must be 'standard' or 'enhanced'.")

    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} does not exist. Copy .env.example to .env first.")

    original = env_path.read_text(encoding="utf-8")
    current = _active_env_value(original, "SPEECHMATICS_OPERATING_POINT")
    if current == value:
        return None

    return apply_env_updates_to_file(
        {"SPEECHMATICS_OPERATING_POINT": value},
        env_path=env_path,
    )


def _normalise_speechmatics_auth_mode(auth_mode: str) -> str:
    value = auth_mode.strip().lower()
    aliases = {
        "api": "api_key",
        "direct": "api_key",
        "jwt": "temporary_key",
        "temporary": "temporary_key",
        "temp": "temporary_key",
    }
    normalized = aliases.get(value, value)
    if normalized not in {"api_key", "temporary_key"}:
        raise ValueError("auth_mode must be 'api_key' or 'temporary_key'.")
    return normalized


def apply_speechmatics_auth_mode(
    auth_mode: str,
    env_path: Path = Path(".env"),
) -> Optional[Path]:
    """Safely switch Speechmatics realtime authentication mode in .env."""

    value = _normalise_speechmatics_auth_mode(auth_mode)

    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} does not exist. Copy .env.example to .env first.")

    original = env_path.read_text(encoding="utf-8")
    current = _active_env_value(original, "SPEECHMATICS_AUTH_MODE")
    if current and _normalise_speechmatics_auth_mode(current) == value:
        return None

    return apply_env_updates_to_file(
        {"SPEECHMATICS_AUTH_MODE": value},
        env_path=env_path,
    )


def list_env_backups(env_path: Path = Path(".env")) -> List[Path]:
    """Return known .env backups newest first."""

    parent = env_path.parent if env_path.parent != Path("") else Path(".")
    pattern = f"{env_path.name}.bak.*"
    backups = [path for path in parent.glob(pattern) if path.is_file()]
    return sorted(backups, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def resolve_env_backup(selector: str, env_path: Path = Path(".env")) -> Path:
    """Resolve 'latest' or a concrete path to an existing backup file."""

    selector = selector.strip()
    if not selector:
        raise ValueError("Backup selector is empty.")
    if selector.lower() == "latest":
        backups = list_env_backups(env_path)
        if not backups:
            raise FileNotFoundError(f"No backups found for {env_path}.")
        return backups[0]

    path = Path(selector).expanduser()
    if not path.is_absolute():
        path = (env_path.parent if env_path.parent != Path("") else Path(".")) / path
    if not path.exists():
        raise FileNotFoundError(f"Backup file not found: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Backup path is not a file: {path}")
    return path


def restore_env_backup(selector: str, env_path: Path = Path(".env")) -> Tuple[Path, Path]:
    """Restore .env from a backup while preserving the current .env first."""

    backup_path = resolve_env_backup(selector, env_path)
    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} does not exist.")

    safety_path = _unique_env_sidecar_path(env_path, "pre-restore")
    shutil.copy2(env_path, safety_path)
    shutil.copy2(backup_path, env_path)
    return backup_path, safety_path


def render_env_backups(backups: List[Path]) -> str:
    lines: List[str] = []
    lines.append("================")
    lines.append(".env backups")
    lines.append("================")
    if not backups:
        lines.append("(no backups found)")
        lines.append("")
        lines.append(
            "Backups are created by --apply-audio-profile, --set-speechmatics-operating-point, "
            "or --set-speechmatics-auth-mode "
            "when .env values actually change."
        )
        return "\n".join(lines)

    for index, backup in enumerate(backups, 1):
        mtime = datetime.fromtimestamp(backup.stat().st_mtime).isoformat(timespec="seconds")
        lines.append(f"{index}. {backup}  ({mtime})")
    lines.append("")
    lines.append("Restore latest: python -m transcriber.cli --restore-env-backup latest")
    lines.append("Restore a file: python -m transcriber.cli --restore-env-backup <path>")
    lines.append(
        "Tip: use the exact .env.bak.* name printed by the command you just ran; "
        "latest may mix audio and Speechmatics changes."
    )
    return "\n".join(lines)


def render_profile_plan(
    plan: ProfilePlan,
    applied_backup: Optional[Path] = None,
    unchanged: bool = False,
) -> str:
    lines: List[str] = []
    title = f"Audio profile: {plan.profile}"
    lines.append("=" * len(title))
    lines.append(title)
    lines.append("=" * len(title))

    if plan.device:
        rate = int(round(plan.device.default_samplerate)) if plan.device.default_samplerate else "unknown"
        lines.append(
            f"Selected device: #{plan.device.index} {plan.device.name} "
            f"({plan.device.hostapi}, default_rate={rate})"
        )
    else:
        lines.append("Selected device: (none)")

    if applied_backup:
        lines.append(f".env backup: {applied_backup}")
    elif unchanged:
        lines.append(".env already matched this profile; no backup was created.")

    if plan.updates:
        lines.append("")
        lines.append("[.env updates]")
        for key, value in plan.updates.items():
            display = value if value else "(blank)"
            lines.append(f"  {key}={display}")

    if plan.notes:
        lines.append("")
        lines.append("[Notes]")
        for note in plan.notes:
            lines.append(f"  - {note}")

    if plan.warnings:
        lines.append("")
        lines.append("[Warnings]")
        for warning in plan.warnings:
            lines.append(f"  - {warning}")

    lines.append("")
    lines.append("[Restore]")
    if applied_backup:
        lines.append(
            f"  元に戻す場合: python -m transcriber.cli --restore-env-backup {applied_backup.name}"
        )
        lines.append("  復元前の現在の .env も .env.pre-restore.* として保存されます。")
    elif unchanged:
        lines.append("  .env は変更していないため、この操作分の復元は不要です。")
    else:
        lines.append(
            "  実際に適用する場合は --apply-audio-profile を使います。"
            " .env の値が変わる場合だけバックアップを作ります。"
        )
    lines.append("  OS側の既定再生デバイスや音量ミキサーは、このコマンドでは変更しません。")
    return "\n".join(lines)
