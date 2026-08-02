import tempfile
import unittest
from pathlib import Path

import numpy as np

from sentrylab.services.evidence_recorder import BufferedFrame, EvidenceClipRecorder


class FakeIncidentRepository:
    def __init__(self):
        self.clips = []

    def set_clip(self, incident_id, clip_path, overlay_enabled):
        self.clips.append((incident_id, clip_path, overlay_enabled))


class EvidenceClipRecorderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = FakeIncidentRepository()

    def tearDown(self):
        self.temp.cleanup()

    def test_three_before_and_seven_after_are_sent_to_writer(self):
        writes = []

        def writer(path, frames, start_at, end_at, fps):
            writes.append((path, frames, start_at, end_at, fps))
            path.write_bytes(b"playable")
            return True

        recorder = EvidenceClipRecorder(
            "CAM 01",
            "restricted_zone",
            Path(self.temp.name),
            self.repository,
            fps=10,
            frame_encoder=lambda frame: bytes([frame]),
            clip_writer=writer,
        )
        for timestamp in (-3.0, -2.0, -1.0):
            recorder.add_frame(int(timestamp + 4), timestamp)
        self.assertTrue(recorder.trigger(7, 0.0, True))
        self.assertFalse(recorder.trigger(7, 0.0, True))
        for timestamp in range(0, 8):
            recorder.add_frame(timestamp + 10, float(timestamp))
        recorder.stop()

        self.assertEqual(len(writes), 1)
        _, frames, start_at, end_at, fps = writes[0]
        self.assertEqual(start_at, -3.0)
        self.assertEqual(end_at, 7.0)
        self.assertEqual(fps, 10.0)
        self.assertEqual(frames[0].timestamp, -3.0)
        self.assertEqual(frames[-1].timestamp, 7.0)
        self.assertEqual(
            self.repository.clips,
            [(7, "incident-000007_cam-01_restricted-zone-h264.mp4", True)],
        )

    def test_h264_writer_produces_ten_second_playable_clip(self):
        import cv2

        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[:, :, 1] = 200
        ok, encoded = cv2.imencode(".jpg", frame)
        self.assertTrue(ok)
        samples = [
            BufferedFrame(float(timestamp), encoded.tobytes())
            for timestamp in range(-3, 8)
        ]
        path = Path(self.temp.name) / "playable.mp4"
        self.assertTrue(
            EvidenceClipRecorder._write_mp4(path, samples, -3.0, 7.0, 10.0)
        )

        capture = cv2.VideoCapture(str(path))
        try:
            self.assertTrue(capture.isOpened())
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            self.assertEqual(frame_count, 100)
            self.assertAlmostEqual(fps, 10.0, places=1)
            self.assertAlmostEqual(frame_count / fps, 10.0, places=1)
        finally:
            capture.release()


if __name__ == "__main__":
    unittest.main()
