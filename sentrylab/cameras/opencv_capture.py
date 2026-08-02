"""OpenCV capture adapter. OpenCV is imported only when a camera starts."""

from __future__ import annotations

from typing import Any


class OpenCVCapture:
    def __init__(
        self,
        source: int | str,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        codec: str | None = None,
        backend: str | None = None,
    ) -> None:
        import cv2

        backend_ids = {
            "dshow": getattr(cv2, "CAP_DSHOW", 0),
            "msmf": getattr(cv2, "CAP_MSMF", 0),
        }
        backend_id = backend_ids.get(str(backend or "").lower())
        is_rtsp = isinstance(source, str) and source.lower().startswith("rtsp://")
        if is_rtsp:
            # A bounded read lets CameraWorker stop cooperatively. Calling
            # VideoCapture.release() from a second thread while FFmpeg is in
            # read() can terminate the whole Python process on Windows.
            parameters = [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1500,
            ]
            self._capture = cv2.VideoCapture(
                source,
                getattr(cv2, "CAP_FFMPEG", 0),
                parameters,
            )
        else:
            self._capture = (
                cv2.VideoCapture(source, backend_id)
                if backend_id
                else cv2.VideoCapture(source)
            )
        if codec and len(codec) == 4:
            self._capture.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*codec.upper()),
            )
        if width:
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        if fps:
            self._capture.set(cv2.CAP_PROP_FPS, float(fps))
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def is_opened(self) -> bool:
        return bool(self._capture.isOpened())

    def read(self) -> tuple[bool, Any]:
        return self._capture.read()

    def release(self) -> None:
        self._capture.release()
