import time
import json
import tempfile
import unittest
from pathlib import Path

from sentrylab.cameras.base import CameraDefinition, CameraState
from sentrylab.cameras.manager import CameraManager
from sentrylab.cameras.worker import CameraWorker


class FakeFrame:
    def __init__(self, value):
        self.value = value

    def copy(self):
        return FakeFrame(self.value)


class FakeCapture:
    def __init__(self, frames, opened=True):
        self.frames = list(frames)
        self.opened = opened
        self.released = False

    def is_opened(self):
        return self.opened

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        return False, None

    def release(self):
        self.released = True


def wait_until(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class CameraWorkerTest(unittest.TestCase):
    def setUp(self):
        self.definition = CameraDefinition(
            camera_id="CAM 02",
            name="Test USB",
            camera_type="usb",
            enabled=True,
            device_index=0,
        )

    def test_worker_keeps_only_latest_frame(self):
        captures = []

        def factory(_source):
            capture = FakeCapture([FakeFrame(1), FakeFrame(2)])
            captures.append(capture)
            return capture

        worker = CameraWorker(self.definition, factory, 0.5)
        worker.start()
        self.assertTrue(wait_until(lambda: worker.get_latest_frame() is not None))
        self.assertTrue(wait_until(lambda: worker.status()["sequence"] >= 2))
        latest = worker.get_latest_frame()
        worker.stop()

        self.assertEqual(latest.frame.value, 2)
        self.assertEqual(latest.sequence, 2)
        self.assertTrue(captures[0].released)

    def test_worker_reconnects_after_open_failure(self):
        attempts = []

        def factory(_source):
            attempts.append(1)
            if len(attempts) == 1:
                return FakeCapture([], opened=False)
            return FakeCapture([FakeFrame("connected")])

        worker = CameraWorker(self.definition, factory, 0.01)
        worker.start()
        self.assertTrue(wait_until(lambda: worker.get_latest_frame() is not None))
        latest = worker.get_latest_frame()
        status = worker.status()
        worker.stop()

        self.assertEqual(latest.frame.value, "connected")
        self.assertGreaterEqual(status["reconnect_count"], 1)
        self.assertGreaterEqual(len(attempts), 2)

    def test_manager_returns_same_worker_for_camera(self):
        manager = CameraManager([self.definition], lambda _source: FakeCapture([]))
        first = manager.get_or_create("CAM 02")
        second = manager.get_or_create("CAM 02")
        self.assertIs(first, second)

    def test_status_reports_delivered_resolution_and_capture_fps(self):
        class Image:
            shape = (1080, 1920, 3)

            def copy(self):
                return self

        worker = CameraWorker(self.definition, lambda _definition: FakeCapture([]))
        worker._store_frame(Image(), 10.0)
        worker._store_frame(Image(), 10.1)
        status = worker.status()
        self.assertEqual((status["width"], status["height"]), (1920, 1080))
        self.assertAlmostEqual(status["capture_fps"], 10.0, places=1)

    def test_camera_file_loads_usb_capture_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cameras.json"
            path.write_text(json.dumps({"cameras": [{
                "id": "CAM 02", "name": "USB", "type": "usb",
                "enabled": True, "device_index": 0,
                "width": 1920, "height": 1080, "fps": 30,
                "codec": "MJPG", "backend": "dshow",
            }]}), encoding="utf-8")
            manager = CameraManager.from_file(path)
            definition = manager._definitions["CAM 02"]
            self.assertEqual((definition.width, definition.height), (1920, 1080))
            self.assertEqual(definition.fps, 30.0)
            self.assertEqual(definition.codec, "MJPG")
            self.assertEqual(definition.backend, "dshow")

    def test_disabled_camera_does_not_create_worker(self):
        disabled = CameraDefinition(
            camera_id="CAM 03",
            name="Future",
            camera_type="unconfigured",
            enabled=False,
        )
        manager = CameraManager([disabled], lambda _source: FakeCapture([]))
        manager.start_enabled()
        self.assertEqual(manager.status("CAM 03")["state"], CameraState.DISABLED)


class CameraApiTest(unittest.TestCase):
    def test_camera_list_does_not_start_capture(self):
        from sentrylab import create_app

        app = create_app()
        manager = app.extensions["camera_manager"]
        client = app.test_client()
        response = client.get("/api/cameras")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["cameras"]), 3)
        self.assertEqual(manager._workers, {})


if __name__ == "__main__":
    unittest.main()
