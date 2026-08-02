"""Camera contracts that do not depend on OpenCV."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class CameraState(StrEnum):
    DISABLED = "DISABLED"
    UNCONFIGURED = "UNCONFIGURED"
    STOPPED = "STOPPED"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CameraDefinition:
    camera_id: str
    name: str
    camera_type: str
    enabled: bool
    device_index: int | None = None
    rtsp_url_env: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    backend: str | None = None

    def source(self) -> int | str | None:
        if self.camera_type == "usb":
            return self.device_index
        if self.camera_type == "rtsp" and self.rtsp_url_env:
            return os.getenv(self.rtsp_url_env)
        return None

    def is_configured(self) -> bool:
        return self.source() is not None


@dataclass(frozen=True)
class LatestFrame:
    frame: Any
    sequence: int
    captured_at: float


class CaptureDevice(Protocol):
    def is_opened(self) -> bool: ...

    def read(self) -> tuple[bool, Any]: ...

    def release(self) -> None: ...
