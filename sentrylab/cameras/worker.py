"""One capture thread and one latest-frame slot per camera."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from sentrylab.cameras.base import (
    CameraDefinition,
    CameraState,
    CaptureDevice,
    LatestFrame,
)


LOGGER = logging.getLogger(__name__)
CaptureFactory = Callable[[CameraDefinition], CaptureDevice]


class CameraWorker:
    def __init__(
        self,
        definition: CameraDefinition,
        capture_factory: CaptureFactory,
        reconnect_delay_seconds: float = 1.0,
    ) -> None:
        self.definition = definition
        self._capture_factory = capture_factory
        self._reconnect_delay = max(0.01, float(reconnect_delay_seconds))

        self._state_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._stop_event = threading.Event()

        self._thread: threading.Thread | None = None
        self._state = CameraState.STOPPED
        self._last_error: str | None = None
        self._latest: LatestFrame | None = None
        self._sequence = 0
        self._reconnect_count = 0
        self._frame_times = deque(maxlen=120)

    @property
    def camera_id(self) -> str:
        return self.definition.camera_id

    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name=f"camera-{self.camera_id.replace(' ', '-').lower()}",
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._start_lock:
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        self._set_state(CameraState.STOPPED)

    def _set_state(self, state: CameraState, error: str | None = None) -> None:
        with self._state_lock:
            self._state = state
            self._last_error = error

    def _store_frame(self, frame, captured_at: float) -> None:
        stored = frame.copy() if hasattr(frame, "copy") else frame
        with self._frame_lock:
            self._sequence += 1
            self._latest = LatestFrame(stored, self._sequence, captured_at)
            self._frame_times.append(float(captured_at))

    def get_latest_frame(self, copy: bool = True) -> LatestFrame | None:
        with self._frame_lock:
            latest = self._latest
            if latest is None:
                return None
            frame = latest.frame
            if copy and hasattr(frame, "copy"):
                frame = frame.copy()
            return LatestFrame(frame, latest.sequence, latest.captured_at)

    def status(self) -> dict:
        with self._state_lock, self._frame_lock:
            latest = self._latest
            shape = getattr(latest.frame, "shape", None) if latest else None
            capture_fps = 0.0
            if len(self._frame_times) >= 2:
                elapsed = self._frame_times[-1] - self._frame_times[0]
                if elapsed > 0:
                    capture_fps = (len(self._frame_times) - 1) / elapsed
            return {
                "camera_id": self.camera_id,
                "name": self.definition.name,
                "type": self.definition.camera_type,
                "enabled": self.definition.enabled,
                "configured": self.definition.is_configured(),
                "state": self._state.value,
                "sequence": latest.sequence if latest else 0,
                "last_frame_at": latest.captured_at if latest else None,
                "reconnect_count": self._reconnect_count,
                "last_error": self._last_error,
                "width": int(shape[1]) if shape is not None and len(shape) >= 2 else None,
                "height": int(shape[0]) if shape is not None and len(shape) >= 2 else None,
                "capture_fps": round(capture_fps, 2),
                "requested_width": self.definition.width,
                "requested_height": self.definition.height,
                "requested_fps": self.definition.fps,
            }

    def _run(self) -> None:
        source = self.definition.source()
        if not self.definition.enabled:
            self._set_state(CameraState.DISABLED)
            return
        if source is None:
            self._set_state(CameraState.UNCONFIGURED, "Camera source is not configured")
            return

        first_attempt = True
        while not self._stop_event.is_set():
            self._set_state(
                CameraState.CONNECTING if first_attempt else CameraState.RECONNECTING
            )
            capture = None
            try:
                capture = self._capture_factory(self.definition)
                if not capture.is_opened():
                    raise ConnectionError("Camera connection could not be opened")

                self._set_state(CameraState.ONLINE)
                while not self._stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        raise ConnectionError("Camera frame read failed")
                    self._store_frame(frame, time.time())
            except Exception as error:
                if self._stop_event.is_set():
                    break
                LOGGER.warning("%s capture error: %s", self.camera_id, error)
                with self._state_lock:
                    self._reconnect_count += 1
                self._set_state(CameraState.RECONNECTING, str(error))
            finally:
                if capture is not None:
                    try:
                        capture.release()
                    except Exception:
                        LOGGER.exception("%s capture release failed", self.camera_id)

            first_attempt = False
            self._stop_event.wait(self._reconnect_delay)

        self._set_state(CameraState.STOPPED)
