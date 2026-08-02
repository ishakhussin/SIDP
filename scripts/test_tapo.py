"""Read one Tapo RTSP frame without printing the credential-bearing URL."""

from __future__ import annotations

import os
import sys
import time


def main() -> int:
    url = os.getenv("SENTRYLAB_CAM01_RTSP_URL")
    if not url:
        print("CAM 01 is not configured.", file=sys.stderr)
        return 2

    import cv2

    capture = cv2.VideoCapture(url)
    try:
        if not capture.isOpened():
            print("CAM 01 connection could not be opened.", file=sys.stderr)
            return 3

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            ok, frame = capture.read()
            if ok and frame is not None and frame.size:
                height, width = frame.shape[:2]
                reported_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
                print(f"CAM 01 ONLINE: {width}x{height} at {reported_fps:.1f} reported FPS")
                return 0
        print("CAM 01 opened but did not return a frame within 10 seconds.", file=sys.stderr)
        return 4
    finally:
        capture.release()


if __name__ == "__main__":
    raise SystemExit(main())
