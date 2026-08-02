import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "test_tapo.py"
SPEC = importlib.util.spec_from_file_location("test_tapo_script", SCRIPT_PATH)
TAPO_SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TAPO_SCRIPT)


class FakeCapture:
    def __init__(self, opened=True, frame=None):
        self.opened = opened
        self.frame = frame
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        return self.frame is not None, self.frame

    def get(self, _property):
        return 25.0

    def release(self):
        self.released = True


class TapoSetupTest(unittest.TestCase):
    def test_missing_rtsp_environment_variable_fails_without_loading_opencv(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(TAPO_SCRIPT.main(), 2)

    def test_valid_frame_reports_success_and_releases_camera(self):
        capture = FakeCapture(frame=np.zeros((720, 1280, 3), dtype=np.uint8))
        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_FPS=5,
            VideoCapture=lambda _url: capture,
        )
        with patch.dict(os.environ, {"SENTRYLAB_CAM01_RTSP_URL": "rtsp://hidden"}, clear=True):
            with patch.dict(sys.modules, {"cv2": fake_cv2}):
                result = TAPO_SCRIPT.main()

        self.assertEqual(result, 0)
        self.assertTrue(capture.released)


if __name__ == "__main__":
    unittest.main()
