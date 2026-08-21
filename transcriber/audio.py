"""Audio capture utilities."""

from __future__ import annotations

import asyncio
import logging
import math
import queue
import time
from contextlib import asynccontextmanager
from array import array
from typing import Any, AsyncGenerator, List, Optional

try:
    import audioop
except ImportError:  # pragma: no cover - exercised on Python 3.13+
    audioop = None  # type: ignore[assignment]

import numpy as np
import sounddevice as sd

from .config import AudioInputConfig


class AudioCaptureError(Exception):
    """Raised when the audio subsystem cannot be initialised."""


def _pcm16_array(chunk: bytes) -> np.ndarray:
    return np.frombuffer(chunk, dtype=np.int16)


def _resample_pcm16_mono(
    chunk: bytes,
    source_rate: int,
    target_rate: int,
    state: Optional[Any],
) -> tuple[bytes, Optional[Any]]:
    if audioop is not None:
        return audioop.ratecv(chunk, 2, 1, source_rate, target_rate, state)

    samples = _pcm16_array(chunk)
    if samples.size == 0 or source_rate == target_rate:
        return chunk, state

    output_count = max(1, int(round(samples.size * target_rate / source_rate)))
    if samples.size == 1:
        resampled = np.repeat(samples, output_count)
    else:
        src_x = np.linspace(0.0, 1.0, num=samples.size, endpoint=True)
        dst_x = np.linspace(0.0, 1.0, num=output_count, endpoint=True)
        resampled = np.interp(dst_x, src_x, samples.astype(np.float32))
    resampled = np.clip(np.rint(resampled), -32768, 32767).astype(np.int16)
    return resampled.tobytes(), state


def _pcm16_rms(chunk: bytes) -> int:
    if audioop is not None:
        return audioop.rms(chunk, 2)
    samples = _pcm16_array(chunk).astype(np.float64)
    if samples.size == 0:
        return 0
    return int(math.sqrt(float(np.mean(samples * samples))))


def _pcm16_peak(chunk: bytes) -> int:
    if audioop is not None:
        return audioop.max(chunk, 2)
    samples = _pcm16_array(chunk)
    if samples.size == 0:
        return 0
    return int(np.max(np.abs(samples.astype(np.int32))))


class AudioChunkStream:
    """Async iterator producing raw PCM audio chunks with automatic device reconnection."""

    def __init__(self, config: AudioInputConfig, check_interval: float = 2.0) -> None:
        self.config = config
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=10)
        self._stream: Optional[sd.RawInputStream] = None
        self._stopped = asyncio.Event()
        self._check_interval = check_interval
        self._current_device: Optional[int] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._stream_error = asyncio.Event()
        self._reconnect_lock = asyncio.Lock()
        self._last_chunk_time: float = 0.0
        self._chunk_timeout: float = 5.0  # Consider stream dead if no chunks for 5 seconds
        self._last_missing_device_index: Optional[int] = None
        self._last_named_resolution: Optional[int] = None
        self._last_index_mismatch: Optional[tuple[int, str, int]] = None
        self._needs_downmix = self.config.channels > 1
        self._downmix_warning_logged = False
        self._fatal_error: Optional[AudioCaptureError] = None
        self._target_sample_rate = self.config.sample_rate
        self._device_sample_rate = self.config.device_sample_rate or self.config.sample_rate
        if self.config.device_sample_rate is None:
            logging.info(
                "AUDIO_DEVICE_SAMPLE_RATE not provided; using %s Hz for both device and engine.",
                self._target_sample_rate,
            )
        self._resample_needed = self._device_sample_rate != self._target_sample_rate
        self._resample_state: Optional[Any] = None
        self._resample_buffer = bytearray()
        self._target_frame_count = max(
            1, int(round(self._target_sample_rate * self.config.chunk_duration_seconds))
        )
        self._device_frame_count = max(
            1, int(round(self._device_sample_rate * self.config.chunk_duration_seconds))
        )
        self._target_chunk_bytes = self._target_frame_count * 2  # mono int16 frames
        self._device_chunk_seconds = self._device_frame_count / self._device_sample_rate
        self._chunk_timeout = max(5.0, self.config.chunk_duration_seconds * 4.0)
        self._level_monitor_enabled = self.config.level_monitor_enabled
        self._silence_threshold_dbfs = self.config.level_silence_threshold_dbfs
        self._silence_duration_required = self.config.level_silence_duration_seconds
        self._clip_threshold_dbfs = self.config.level_clip_threshold_dbfs
        self._clip_hold_seconds = self.config.level_clip_hold_seconds
        if self._clip_threshold_dbfs > 0.0:
            self._clip_threshold_dbfs = 0.0
        if self._silence_threshold_dbfs > -1.0:
            self._silence_threshold_dbfs = -1.0
        self._silence_accumulator = 0.0
        self._clip_accumulator = 0.0
        self._last_silence_warning_ts = 0.0
        self._last_clip_warning_ts = 0.0
        self._level_warning_cooldown = 15.0

    def _get_default_input_device(self) -> Optional[int]:
        """Best-effort lookup of the current system default input device."""
        try:
            default_input = sd.query_devices(kind="input")
            if default_input:
                return default_input.get("index")
        except Exception as exc:
            logging.warning("Failed to query default input device: %s", exc)
        return None

    @staticmethod
    def _device_name_matches(preferred_name: str, actual_name: str) -> bool:
        preferred = " ".join(preferred_name.lower().split())
        actual = " ".join(actual_name.lower().split())
        if not preferred or not actual:
            return False
        return preferred == actual or preferred in actual or actual in preferred

    @staticmethod
    def _hostapi_name(hostapi_index: object) -> str:
        try:
            index = int(hostapi_index)
            hostapis = sd.query_hostapis()
            if 0 <= index < len(hostapis):
                return str(hostapis[index].get("name", index))
        except Exception:  # noqa: BLE001
            return ""
        return ""

    def _find_named_input_device(self, preferred_name: Optional[str]) -> Optional[int]:
        """Find a preferred input device by name when saved indexes shift."""

        preferred = (preferred_name or "").strip()
        if not preferred:
            return None

        try:
            devices = sd.query_devices()
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to enumerate audio devices while resolving %r: %s", preferred, exc)
            return None

        candidates: List[tuple[int, int, int, int, int, str]] = []
        for index, device in enumerate(devices):
            try:
                inputs = int(device.get("max_input_channels", 0))
            except Exception:  # noqa: BLE001
                inputs = 0
            if inputs <= 0:
                continue

            actual_name = str(device.get("name", ""))
            if not self._device_name_matches(preferred, actual_name):
                continue

            hostapi_name = self._hostapi_name(device.get("hostapi", -1)).lower()
            actual_lower = actual_name.lower()
            default_rate = device.get("default_samplerate")
            try:
                rate_matches = int(round(float(default_rate))) == self._device_sample_rate
            except Exception:  # noqa: BLE001
                rate_matches = False

            candidates.append(
                (
                    0 if actual_name.lower() == preferred.lower() else 1,
                    0 if any(api in hostapi_name for api in ("wasapi", "pulse", "pipewire")) else 1,
                    0 if any(term in actual_lower for term in ("monitor", "loopback", "cable output")) else 1,
                    0 if rate_matches else 1,
                    index,
                    actual_name,
                )
            )

        if not candidates:
            return None

        _exact, _hostapi, _loopback, _rate, index, actual_name = sorted(candidates)[0]
        if index != self.config.device_index and self._last_named_resolution != index:
            logging.info(
                "Resolved preferred audio device by name: #%s %s.",
                index,
                actual_name,
            )
        self._last_named_resolution = index
        return index

    def _get_preferred_configured_device(self) -> Optional[int]:
        """Resolve the configured device, preferring stable names over stale indexes."""

        preferred_name = (
            self.config.linux_loopback_sink
            or self.config.windows_loopback_device
            or self.config.mac_loopback_device
        )
        named_device = self._find_named_input_device(preferred_name)
        if self.config.device_index is not None:
            try:
                configured_info = sd.query_devices(self.config.device_index, kind="input")
                self._last_missing_device_index = None
            except Exception as exc:
                if self._last_missing_device_index != self.config.device_index:
                    logging.warning(
                        "Configured audio device index %s unavailable (%s).",
                        self.config.device_index,
                        exc,
                    )
                    self._last_missing_device_index = self.config.device_index
                return named_device

            configured_name = str(configured_info.get("name", ""))
            if preferred_name and not self._device_name_matches(preferred_name, configured_name):
                if named_device is not None:
                    mismatch = (self.config.device_index, configured_name, named_device)
                    if self._last_index_mismatch != mismatch:
                        logging.warning(
                            "AUDIO_DEVICE_INDEX=%s points to %r, but preferred device %r "
                            "now resolves to device index %s. Using the named device.",
                            self.config.device_index,
                            configured_name,
                            preferred_name,
                            named_device,
                        )
                    self._last_index_mismatch = mismatch
                    return named_device
            self._last_index_mismatch = None
            return self.config.device_index

        return named_device

    def _get_effective_device(self) -> Optional[int]:
        """Get the device index to use (configured or system default)."""
        preferred = self._get_preferred_configured_device()
        if preferred is not None:
            return preferred
        return self._get_default_input_device()

    def _callback(self, indata: bytes, frames: int, _time, status: sd.CallbackFlags) -> None:
        if status:
            if status.input_overflow:
                logging.warning("Audio stream input overflow")
            elif status.input_underflow:
                logging.warning("Audio stream input underflow")
            else:
                logging.warning("Audio stream status: %s", status)
                # Signal that stream may need restart
                self._stream_error.set()

        chunk = bytes(indata)
        if self._needs_downmix:
            if not self._downmix_warning_logged:
                logging.info(
                    "Downmixing %s-channel audio to mono to satisfy backend requirements.",
                    self.config.channels,
                )
                self._downmix_warning_logged = True
            chunk = self._downmix_to_mono(chunk)

        data = chunk
        if self._resample_needed:
            try:
                data, self._resample_state = _resample_pcm16_mono(
                    chunk,
                    self._device_sample_rate,
                    self._target_sample_rate,
                    self._resample_state,
                )
            except Exception as exc:  # noqa: BLE001
                logging.error("Audio resampling failed: %s", exc)
                self._stream_error.set()
                return

        if not self._resample_needed and len(data) == self._target_chunk_bytes:
            self._publish_chunk(data)
            return

        if data:
            self._resample_buffer.extend(data)

        while len(self._resample_buffer) >= self._target_chunk_bytes:
            ready = bytes(self._resample_buffer[: self._target_chunk_bytes])
            del self._resample_buffer[: self._target_chunk_bytes]
            self._publish_chunk(ready)

    def _downmix_to_mono(self, data: bytes) -> bytes:
        """Average multi-channel int16 PCM data down to mono."""

        sample_width = 2  # pcm_s16le
        total_samples = len(data) // sample_width
        channels = max(1, self.config.channels)
        if channels == 1 or total_samples == 0:
            return data

        frames = total_samples // channels
        src = array("h")
        src.frombytes(data)
        mono = array("h", [0]) * frames
        idx = 0
        for frame in range(frames):
            acc = 0
            for _ in range(channels):
                acc += src[idx]
                idx += 1
            mono[frame] = int(acc / channels)
        return mono.tobytes()

    def _publish_chunk(self, chunk: bytes) -> None:
        if not chunk:
            return

        duration = len(chunk) / (2 * self._target_sample_rate)
        self._last_chunk_time = time.time()

        if self._level_monitor_enabled:
            self._analyse_levels(chunk, duration)

        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            try:
                _ = self._queue.get_nowait()
                self._queue.put_nowait(chunk)
                logging.debug("Dropped one audio chunk to keep up with realtime processing.")
            except queue.Empty:
                logging.debug("Audio buffer overflow handled, but queue empty when trimming.")

    def _analyse_levels(self, chunk: bytes, duration: float) -> None:
        """Track RMS/peak levels and log anomalies."""

        # RMS in dBFS
        rms = _pcm16_rms(chunk)
        if rms <= 0:
            level_db = float("-inf")
        else:
            level_db = 20.0 * math.log10(rms / 32767.0)

        now = time.time()
        if level_db <= self._silence_threshold_dbfs:
            self._silence_accumulator += duration
            if (
                self._silence_accumulator >= self._silence_duration_required
                and now - self._last_silence_warning_ts >= self._level_warning_cooldown
            ):
                logging.warning(
                    "Input audio level below %.1f dBFS for %.1f seconds; "
                    "verify loopback routing or microphone gain.",
                    self._silence_threshold_dbfs,
                    self._silence_accumulator,
                )
                self._last_silence_warning_ts = now
                self._silence_accumulator = 0.0
        else:
            self._silence_accumulator = 0.0

        peak = _pcm16_peak(chunk)
        if peak <= 0:
            peak_db = float("-inf")
        else:
            peak_db = 20.0 * math.log10(peak / 32767.0)

        if peak_db >= self._clip_threshold_dbfs:
            self._clip_accumulator += duration
            if (
                self._clip_accumulator >= self._clip_hold_seconds
                and now - self._last_clip_warning_ts >= self._level_warning_cooldown
            ):
                logging.warning(
                    "Input audio peak at %.1f dBFS for %.1f seconds; "
                    "attenuate the source to prevent clipping.",
                    peak_db,
                    self._clip_accumulator,
                )
                self._last_clip_warning_ts = now
                self._clip_accumulator = 0.0
        else:
            self._clip_accumulator = 0.0
    def _reset_resampler(self) -> None:
        self._resample_state = None
        self._resample_buffer.clear()

    def _reset_level_state(self) -> None:
        self._silence_accumulator = 0.0
        self._clip_accumulator = 0.0

    def _device_label(self, device: Optional[int]) -> str:
        """Human readable label for logging."""
        if device is None:
            return "system default input"
        return f"device index {device}"

    def _open_stream(self, device: Optional[int]) -> None:
        """Instantiate and start the RawInputStream for a specific device."""
        device_info = None
        if device is not None:
            device_info = sd.query_devices(device, kind="input")
        else:
            try:
                device_info = sd.query_devices(kind="input")
            except Exception:
                device_info = None

        device_name = device_info.get("name") if device_info else "default"
        logging.info("Starting audio stream on %s (%s)", device_name, self._device_label(device))

        blocksize = (
            self.config.blocksize if self.config.blocksize is not None else self._device_frame_count
        )
        stream = sd.RawInputStream(
            samplerate=self._device_sample_rate,
            channels=self.config.channels,
            dtype="int16",
            callback=self._callback,
            blocksize=blocksize,
            device=device,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise

        self._stream = stream
        if device is not None:
            self._current_device = device
        else:
            # Store the resolved index; the change monitor compares against
            # concrete indexes, and a stored None would mismatch forever.
            self._current_device = self._get_default_input_device()
        self._stream_error.clear()
        self._last_chunk_time = time.time()
        logging.info("Audio stream started successfully on %s", device_name)

    def _start_stream(self, device: Optional[int]) -> None:
        """Start the audio stream with the specified device."""
        if self._device_frame_count <= 0 or self._target_frame_count <= 0:
            raise AudioCaptureError("Chunk duration and sample rate produce zero frames.")

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logging.debug("Error closing old stream: %s", exc)

        self._reset_resampler()
        self._reset_level_state()

        attempt_devices: List[Optional[int]] = []

        def add_candidate(candidate: Optional[int]) -> None:
            if candidate not in attempt_devices:
                attempt_devices.append(candidate)

        preferred = self._get_preferred_configured_device()
        if preferred is not None:
            add_candidate(preferred)
        add_candidate(device)
        if self.config.device_index is not None:
            add_candidate(self.config.device_index)
        if device is not None:
            add_candidate(self._get_default_input_device())
            add_candidate(None)
        if not attempt_devices:
            add_candidate(None)

        last_exc: Optional[Exception] = None
        for candidate in attempt_devices:
            try:
                self._open_stream(candidate)
                if self._resample_needed:
                    logging.info(
                        "Resampling audio from %s Hz -> %s Hz (chunk %.3fs).",
                        self._device_sample_rate,
                        self._target_sample_rate,
                        self.config.chunk_duration_seconds,
                    )
                elif abs(self._device_chunk_seconds - self.config.chunk_duration_seconds) > 0.01:
                    logging.debug(
                        "Device chunk duration %.4fs differs from target %.4fs; buffering to compensate.",
                        self._device_chunk_seconds,
                        self.config.chunk_duration_seconds,
                    )
                return
            except Exception as exc:
                last_exc = exc
                logging.warning(
                    "Failed to start audio stream on %s: %s",
                    self._device_label(candidate),
                    exc,
                )

        raise AudioCaptureError(f"Failed to initialise audio input: {last_exc}") from last_exc

    def _is_stream_alive(self) -> bool:
        """Check if the stream is receiving data."""
        if self._last_chunk_time == 0.0:
            # Stream just started, give it time
            return True
        time_since_last_chunk = time.time() - self._last_chunk_time
        return time_since_last_chunk < self._chunk_timeout

    async def _monitor_device_changes(self) -> None:
        """Monitor for device changes and reconnect if necessary."""
        while not self._stopped.is_set():
            try:
                await asyncio.sleep(self._check_interval)

                if self._stopped.is_set():
                    break

                # Check if stream encountered errors
                if self._stream_error.is_set():
                    logging.warning("Audio stream error detected, attempting reconnect...")
                    async with self._reconnect_lock:
                        try:
                            self._start_stream(self._current_device)
                            logging.info("Audio stream reconnected after error")
                        except Exception as exc:
                            logging.error("Failed to reconnect after stream error: %s", exc)
                            self._register_fatal_error(AudioCaptureError(str(exc)))
                    continue

                # Check if stream stopped receiving data (health check)
                if not self._is_stream_alive() and self._stream is not None:
                    logging.warning(
                        "Audio stream appears dead (no data for %.1f seconds), attempting reconnect...",
                        self._chunk_timeout,
                    )
                    async with self._reconnect_lock:
                        try:
                            # Try to reconnect to current device first
                            self._start_stream(self._current_device)
                            logging.info("Audio stream reconnected after timeout")
                        except Exception as exc:
                            logging.error("Failed to reconnect after timeout: %s", exc)
                            # If reconnect fails, try switching to a different available device
                            try:
                                new_device = self._get_effective_device()
                                if new_device != self._current_device:
                                    logging.info("Trying alternate device: %s", new_device)
                                    self._start_stream(new_device)
                            except Exception as retry_exc:
                                logging.error("Failed to reconnect to alternate device: %s", retry_exc)
                                self._register_fatal_error(AudioCaptureError(str(retry_exc)))
                    continue

                # Check if default device changed (only if not using explicit device_index)
                if self.config.device_index is None:
                    current_default = self._get_effective_device()
                    if current_default is not None and current_default != self._current_device:
                        logging.info(
                            "Default audio device changed from %s to %s, reconnecting...",
                            self._current_device,
                            current_default,
                        )
                        async with self._reconnect_lock:
                            try:
                                self._start_stream(current_default)
                                logging.info("Audio stream reconnected to new device")
                            except Exception as exc:
                                logging.error("Failed to reconnect to new device: %s", exc)
                                self._register_fatal_error(AudioCaptureError(str(exc)))
                else:
                    preferred = self._get_preferred_configured_device()
                    if preferred is not None and self._current_device != preferred:
                        try:
                            sd.query_devices(preferred, kind="input")
                        except Exception:
                            # Preferred device still unavailable; keep current fallback
                            pass
                        else:
                            logging.info(
                                "Configured audio device %s is available again; switching back.",
                                preferred,
                            )
                            async with self._reconnect_lock:
                                try:
                                    self._start_stream(preferred)
                                    self._last_missing_device_index = None
                                    logging.info(
                                        "Audio stream reconnected to configured device index %s",
                                        preferred,
                                    )
                                except Exception as exc:
                                    logging.error(
                                        "Failed to reconnect to configured device %s: %s",
                                        preferred,
                                        exc,
                                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logging.exception("Error in device monitor: %s", exc)
                self._register_fatal_error(AudioCaptureError(str(exc)))

    def _register_fatal_error(self, error: AudioCaptureError) -> None:
        if self._fatal_error is not None:
            return
        self._fatal_error = error
        self._stopped.set()
        self._reset_resampler()

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:  # noqa: BLE001
                logging.debug("Error closing stream after fatal error: %s", exc)
            finally:
                self._stream = None

        try:
            self._queue.put_nowait(b"")
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(b"")
            except queue.Full:
                pass

    def stop(self) -> None:
        """Stop producing chunks and wake any pending reader (idempotent)."""

        self._stopped.set()
        try:
            self._queue.put_nowait(b"")
        except queue.Full:
            pass

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["AudioChunkStream", None]:
        """Context manager that starts and stops the underlying audio stream."""

        # Reset stop flag so the stream can be reused after a clean shutdown.
        self._stopped = asyncio.Event()
        self._fatal_error = None

        # Drop any buffered chunks from a previous run before starting anew.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        initial_device = self._get_effective_device()
        self._start_stream(initial_device)

        # Start device monitoring task
        self._monitor_task = asyncio.create_task(
            self._monitor_device_changes(), name="audio-device-monitor"
        )

        try:
            yield self
        finally:
            self.stop()

            if self._monitor_task is not None:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
                self._monitor_task = None

            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as exc:
                    logging.debug("Error closing stream during cleanup: %s", exc)
                self._stream = None

    async def __anext__(self) -> bytes:
        return await self.next_chunk()

    def __aiter__(self) -> "AudioChunkStream":
        return self

    def _blocking_get(self) -> bytes:
        """Wait for a chunk with a bounded timeout so a stopped stream never
        strands the executor thread inside queue.get()."""

        while True:
            try:
                return self._queue.get(timeout=0.25)
            except queue.Empty:
                if self._stopped.is_set() or self._fatal_error is not None:
                    return b""

    async def next_chunk(self) -> bytes:
        """Await the next audio chunk."""

        loop = asyncio.get_running_loop()
        while True:
            if self._fatal_error is not None:
                raise self._fatal_error
            if self._stopped.is_set():
                raise StopAsyncIteration
            try:
                chunk = await loop.run_in_executor(None, self._blocking_get)
            except Exception as exc:  # pylint: disable=broad-except
                raise AudioCaptureError(f"Failed to read audio chunk: {exc}") from exc
            if self._fatal_error is not None:
                raise self._fatal_error
            if not isinstance(chunk, (bytes, bytearray)):
                # Defensive: ensure callers only receive raw bytes.
                raise AudioCaptureError("Received non-bytes audio chunk from queue.")
            if not chunk:
                # Empty bytes are wake-up sentinels, never real audio.
                if self._stopped.is_set():
                    raise StopAsyncIteration
                continue
            return bytes(chunk)
