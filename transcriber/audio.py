"""Audio capture utilities."""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from contextlib import asynccontextmanager
from array import array
from typing import AsyncGenerator, List, Optional

import sounddevice as sd

from .config import AudioInputConfig


class AudioCaptureError(Exception):
    """Raised when the audio subsystem cannot be initialised."""


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
        self._needs_downmix = self.config.channels > 1
        self._downmix_warning_logged = False
        self._fatal_error: Optional[AudioCaptureError] = None

    def _get_default_input_device(self) -> Optional[int]:
        """Best-effort lookup of the current system default input device."""
        try:
            default_input = sd.query_devices(kind="input")
            if default_input:
                return default_input.get("index")
        except Exception as exc:
            logging.warning("Failed to query default input device: %s", exc)
        return None

    def _get_effective_device(self) -> Optional[int]:
        """Get the device index to use (configured or system default)."""
        if self.config.device_index is not None:
            try:
                sd.query_devices(self.config.device_index, kind="input")
                self._last_missing_device_index = None
                return self.config.device_index
            except Exception as exc:
                if self._last_missing_device_index != self.config.device_index:
                    logging.warning(
                        "Configured audio device index %s unavailable (%s); falling back to default.",
                        self.config.device_index,
                        exc,
                    )
                    self._last_missing_device_index = self.config.device_index
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

        # Update last chunk timestamp
        self._last_chunk_time = time.time()

        chunk = bytes(indata)
        if self._needs_downmix:
            if not self._downmix_warning_logged:
                logging.info(
                    "Downmixing %s-channel audio to mono to satisfy backend requirements.",
                    self.config.channels,
                )
                self._downmix_warning_logged = True
            chunk = self._downmix_to_mono(chunk)

        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            # Drop oldest chunk to prevent runaway latency.
            try:
                _ = self._queue.get_nowait()
                self._queue.put_nowait(chunk)
                logging.debug("Dropped one audio chunk to keep up with realtime processing.")
            except queue.Empty:
                logging.debug("Audio buffer overflow handled, but queue empty when trimming.")

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

    def _device_label(self, device: Optional[int]) -> str:
        """Human readable label for logging."""
        if device is None:
            return "system default input"
        return f"device index {device}"

    def _open_stream(self, frames_per_chunk: int, device: Optional[int]) -> None:
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

        stream = sd.RawInputStream(
            samplerate=self.config.sample_rate,
            channels=self.config.channels,
            dtype="int16",
            callback=self._callback,
            blocksize=(
                frames_per_chunk if self.config.blocksize is None else self.config.blocksize
            ),
            device=device,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise

        self._stream = stream
        self._current_device = device
        self._stream_error.clear()
        self._last_chunk_time = time.time()
        logging.info("Audio stream started successfully on %s", device_name)

    def _start_stream(self, device: Optional[int]) -> None:
        """Start the audio stream with the specified device."""
        frames_per_chunk = int(self.config.sample_rate * self.config.chunk_duration_seconds)
        if frames_per_chunk <= 0:
            raise AudioCaptureError("Chunk duration and sample rate produce zero frames.")

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logging.debug("Error closing old stream: %s", exc)

        attempt_devices: List[Optional[int]] = []

        def add_candidate(candidate: Optional[int]) -> None:
            if candidate not in attempt_devices:
                attempt_devices.append(candidate)

        if self.config.device_index is not None:
            add_candidate(self.config.device_index)
        add_candidate(device)
        if device is not None:
            add_candidate(self._get_default_input_device())
            add_candidate(None)
        if not attempt_devices:
            add_candidate(None)

        last_exc: Optional[Exception] = None
        for candidate in attempt_devices:
            try:
                self._open_stream(frames_per_chunk, candidate)
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
                    if current_default != self._current_device:
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
                    preferred = self.config.device_index
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
            self._stopped.set()

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

    async def next_chunk(self) -> bytes:
        """Await the next audio chunk."""

        if self._fatal_error is not None:
            raise self._fatal_error

        loop = asyncio.get_running_loop()
        if self._stopped.is_set():
            raise StopAsyncIteration
        try:
            chunk = await loop.run_in_executor(None, self._queue.get)
        except Exception as exc:  # pylint: disable=broad-except
            raise AudioCaptureError(f"Failed to read audio chunk: {exc}") from exc
        if self._fatal_error is not None:
            raise self._fatal_error
        if not isinstance(chunk, (bytes, bytearray)):
            # Defensive: ensure callers only receive raw bytes.
            raise AudioCaptureError("Received non-bytes audio chunk from queue.")
        return bytes(chunk)
