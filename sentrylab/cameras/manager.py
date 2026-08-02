"""Registry that guarantees at most one CameraWorker per camera ID."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from sentrylab.cameras.base import CameraDefinition, CameraState
from sentrylab.cameras.opencv_capture import OpenCVCapture
from sentrylab.cameras.worker import CameraWorker, CaptureFactory


def _default_capture_factory(definition: CameraDefinition):
    return OpenCVCapture(
        definition.source(),
        width=definition.width,
        height=definition.height,
        fps=definition.fps,
        codec=definition.codec,
        backend=definition.backend,
    )


class CameraManager:
    def __init__(
        self,
        definitions: list[CameraDefinition],
        capture_factory: CaptureFactory = _default_capture_factory,
        reconnect_delay_seconds: float = 1.0,
    ) -> None:
        ids = [item.camera_id for item in definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("Camera IDs must be unique")

        self._definitions = {item.camera_id: item for item in definitions}
        self._capture_factory = capture_factory
        self._reconnect_delay = reconnect_delay_seconds
        self._workers: dict[str, CameraWorker] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_file(cls, path: Path, **kwargs) -> "CameraManager":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        definitions = []
        for item in payload.get("cameras", []):
            camera_id = str(item.get("id", "")).strip()
            camera_type = str(item.get("type", "")).strip().lower()
            if not camera_id:
                raise ValueError("Every camera requires an ID")
            if camera_type not in {"usb", "rtsp", "unconfigured"}:
                raise ValueError(f"Unsupported camera type for {camera_id}: {camera_type}")
            definitions.append(CameraDefinition(
                camera_id=camera_id,
                name=str(item.get("name", camera_id)),
                camera_type=camera_type,
                enabled=bool(item.get("enabled", False)),
                device_index=item.get("device_index"),
                rtsp_url_env=item.get("rtsp_url_env"),
                width=int(item["width"]) if item.get("width") else None,
                height=int(item["height"]) if item.get("height") else None,
                fps=float(item["fps"]) if item.get("fps") else None,
                codec=str(item["codec"]) if item.get("codec") else None,
                backend=str(item["backend"]) if item.get("backend") else None,
            ))
        return cls(definitions, **kwargs)

    def get_or_create(self, camera_id: str) -> CameraWorker:
        with self._lock:
            definition = self._definitions.get(camera_id)
            if definition is None:
                raise KeyError(f"Unknown camera: {camera_id}")
            worker = self._workers.get(camera_id)
            if worker is None:
                worker = CameraWorker(
                    definition,
                    self._capture_factory,
                    self._reconnect_delay,
                )
                self._workers[camera_id] = worker
            return worker

    def start_enabled(self) -> None:
        for definition in self._definitions.values():
            if definition.enabled and definition.is_configured():
                self.get_or_create(definition.camera_id).start()

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.stop()

    def start_camera(self, camera_id: str) -> dict:
        definition = self._definitions.get(camera_id)
        if definition is None:
            raise KeyError(f"Unknown camera: {camera_id}")
        if not definition.enabled:
            raise ValueError(f"Camera is disabled: {camera_id}")
        if not definition.is_configured():
            raise ValueError(f"Camera source is not configured: {camera_id}")
        worker = self.get_or_create(camera_id)
        worker.start()
        return worker.status()

    def stop_camera(self, camera_id: str) -> dict:
        if camera_id not in self._definitions:
            raise KeyError(f"Unknown camera: {camera_id}")
        worker = self.existing_worker(camera_id)
        if worker is not None:
            worker.stop()
        return self.status(camera_id)

    def existing_worker(self, camera_id: str) -> CameraWorker | None:
        if camera_id not in self._definitions:
            raise KeyError(f"Unknown camera: {camera_id}")
        with self._lock:
            return self._workers.get(camera_id)

    def statuses(self) -> list[dict]:
        output = []
        for definition in self._definitions.values():
            with self._lock:
                worker = self._workers.get(definition.camera_id)
            if worker is not None:
                output.append(worker.status())
                continue

            if not definition.enabled:
                state = CameraState.DISABLED
            elif not definition.is_configured():
                state = CameraState.UNCONFIGURED
            else:
                state = CameraState.STOPPED
            output.append({
                "camera_id": definition.camera_id,
                "name": definition.name,
                "type": definition.camera_type,
                "enabled": definition.enabled,
                "configured": definition.is_configured(),
                "state": state.value,
                "power_on": False,
                "sequence": 0,
                "last_frame_at": None,
                "reconnect_count": 0,
                "last_error": None,
                "width": None,
                "height": None,
                "capture_fps": 0.0,
                "requested_width": definition.width,
                "requested_height": definition.height,
                "requested_fps": definition.fps,
            })
        return output

    def status(self, camera_id: str) -> dict:
        for status in self.statuses():
            if status["camera_id"] == camera_id:
                return status
        raise KeyError(f"Unknown camera: {camera_id}")
