"""MJPEG output built from a camera worker's latest-frame slot."""

from __future__ import annotations

import time
from collections.abc import Iterator


MJPEG_BOUNDARY = b"frame"


def encode_jpeg(frame, quality: int = 80) -> bytes:
    """Encode one frame without importing OpenCV during application import."""
    import cv2

    quality = max(1, min(int(quality), 100))
    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), quality],
    )
    if not ok:
        raise ValueError("Frame could not be JPEG encoded")
    return encoded.tobytes()


def multipart_chunk(jpeg: bytes) -> bytes:
    return (
        b"--" + MJPEG_BOUNDARY
        + b"\r\nContent-Type: image/jpeg\r\n"
        + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
        + jpeg
        + b"\r\n"
    )


def mjpeg_generator(
    worker,
    max_fps: float = 15.0,
    quality: int = 80,
    frame_transform=None,
) -> Iterator[bytes]:
    """Yield each new sequence once; wait instead of replaying stale frames."""
    interval = 1.0 / max(1.0, float(max_fps))
    last_sequence = -1
    next_allowed_at = 0.0

    while True:
        latest = worker.get_latest_frame(copy=True)
        if latest is None or latest.sequence == last_sequence:
            time.sleep(min(0.02, interval))
            continue

        now = time.monotonic()
        if now < next_allowed_at:
            time.sleep(next_allowed_at - now)

        frame = latest.frame
        if frame_transform is not None:
            frame = frame_transform(frame)
        jpeg = encode_jpeg(frame, quality=quality)
        last_sequence = latest.sequence
        next_allowed_at = time.monotonic() + interval
        yield multipart_chunk(jpeg)
