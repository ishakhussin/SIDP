import unittest
from unittest.mock import patch

from sentrylab import create_app
from sentrylab.cameras.base import LatestFrame
from sentrylab.streaming.mjpeg import mjpeg_generator, multipart_chunk


class StubWorker:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0

    def get_latest_frame(self, copy=True):
        if not self.frames:
            return None
        index = min(self.index, len(self.frames) - 1)
        self.index += 1
        return self.frames[index]


class StubManager:
    def __init__(self, worker):
        self.worker = worker

    def existing_worker(self, camera_id):
        if camera_id != "CAM 02":
            raise KeyError(camera_id)
        return self.worker


class StreamingHelperTest(unittest.TestCase):
    def test_multipart_chunk_has_valid_boundary_and_length(self):
        chunk = multipart_chunk(b"jpeg-data")
        self.assertTrue(chunk.startswith(b"--frame\r\n"))
        self.assertIn(b"Content-Type: image/jpeg", chunk)
        self.assertIn(b"Content-Length: 9", chunk)
        self.assertTrue(chunk.endswith(b"jpeg-data\r\n"))

    @patch("sentrylab.streaming.mjpeg.encode_jpeg", return_value=b"encoded")
    def test_generator_skips_repeated_sequence(self, _encode):
        worker = StubWorker([
            LatestFrame("one", 1, 1.0),
            LatestFrame("duplicate", 1, 1.1),
            LatestFrame("two", 2, 2.0),
        ])
        generator = mjpeg_generator(worker, max_fps=1000)
        first = next(generator)
        second = next(generator)
        self.assertIn(b"encoded", first)
        self.assertIn(b"encoded", second)
        self.assertEqual(_encode.call_count, 2)


class StreamingApiTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    @patch("sentrylab.api.cameras.encode_jpeg", return_value=b"snapshot")
    def test_snapshot_uses_existing_latest_frame(self, _encode):
        worker = StubWorker([LatestFrame("frame", 7, 123.5)])
        self.app.extensions["camera_manager"] = StubManager(worker)
        response = self.client.get("/api/cameras/CAM%2002/snapshot")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"snapshot")
        self.assertEqual(response.headers["X-Camera-Sequence"], "7")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_snapshot_does_not_start_camera_runtime(self):
        response = self.client.get("/api/cameras/CAM%2002/snapshot")
        self.assertEqual(response.status_code, 503)
        self.assertIn("not started", response.get_json()["error"])

    @patch("sentrylab.streaming.mjpeg.encode_jpeg", return_value=b"stream-frame")
    def test_stream_returns_mjpeg(self, _encode):
        worker = StubWorker([LatestFrame("frame", 1, 10.0)])
        self.app.extensions["camera_manager"] = StubManager(worker)
        response = self.client.get(
            "/api/cameras/CAM%2002/stream",
            buffered=False,
        )
        first_chunk = next(response.response)
        response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("multipart/x-mixed-replace", response.content_type)
        self.assertIn(b"stream-frame", first_chunk)

    @patch("sentrylab.streaming.mjpeg.encode_jpeg", return_value=b"raw-frame")
    def test_raw_stream_returns_unannotated_shared_frame(self, _encode):
        worker = StubWorker([LatestFrame("frame", 1, 10.0)])
        self.app.extensions["camera_manager"] = StubManager(worker)
        detection_manager = self.app.extensions["detection_manager"]

        with patch.object(detection_manager, "render") as render:
            response = self.client.get(
                "/api/cameras/CAM%2002/raw-stream",
                buffered=False,
            )
            first_chunk = next(response.response)
            response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("multipart/x-mixed-replace", response.content_type)
        self.assertIn(b"raw-frame", first_chunk)
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
