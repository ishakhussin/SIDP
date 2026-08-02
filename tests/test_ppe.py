import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from sentrylab import create_app
from sentrylab.cameras.base import LatestFrame
from sentrylab.config import Settings
from sentrylab.database import Database, DetectorSettingsRepository, IncidentRepository
from sentrylab.detection.ppe import PPEComplianceDetector
from sentrylab.domain.detection import SafetyLevel
from sentrylab.services.ppe_service import PPEComplianceService


class FakeTensor:
    def __init__(self, values):
        self.values = np.asarray(values)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class FakeBoxes:
    def __init__(self, boxes, ids):
        self.xyxy = FakeTensor(boxes)
        self.id = FakeTensor(ids)


class FakeResult:
    def __init__(self, boxes, ids):
        self.boxes = FakeBoxes(boxes, ids)


class FakePersonModel:
    def __init__(self, boxes, ids):
        self.result = FakeResult(boxes, ids)
        self.calls = []

    def track(self, frame, **kwargs):
        self.calls.append(kwargs)
        return [self.result]


class PPEComplianceDetectorTest(unittest.TestCase):
    def test_missing_item_is_warning_for_each_tracked_person(self):
        model = FakePersonModel([[10, 10, 60, 90], [70, 10, 115, 90]], [7, 9])
        probabilities = np.asarray([[0.9, 0.8, 0.2], [0.9, 0.8, 0.7]])
        detector = PPEComplianceDetector(
            Path("unused"), person_model=model,
            classifier=lambda _frame, _boxes: probabilities,
            smooth_frames=1,
        )
        detections = detector.detect(
            np.zeros((100, 120, 3), dtype=np.uint8), "CAM 02", 1.0
        )
        self.assertEqual([item.observation.subject_id for item in detections], ["7", "9"])
        self.assertEqual(detections[0].observation.level, SafetyLevel.WARNING)
        self.assertEqual(detections[0].missing_items, ("gloves",))
        self.assertEqual(detections[1].observation.level, SafetyLevel.SAFE)
        self.assertEqual(model.calls[0]["classes"], [0])

    def test_all_three_items_are_required(self):
        model = FakePersonModel([[10, 10, 60, 90]], [1])
        detector = PPEComplianceDetector(
            Path("unused"), person_model=model,
            classifier=lambda _frame, _boxes: [[0.49, 0.9, 0.9]],
            smooth_frames=1,
        )
        result = detector.detect(np.zeros((100, 100, 3), dtype=np.uint8), "CAM 01", 0)[0]
        self.assertEqual(result.missing_items, ("coat",))


class Worker:
    def __init__(self, frame):
        self.latest = LatestFrame(frame, 1, 0.0)

    def get_latest_frame(self, copy=True):
        return self.latest


class ServiceDetector:
    loaded = True
    device = "test"

    def __init__(self, detections):
        self.detections = detections

    def detect(self, frame, camera_id, timestamp):
        return self.detections

    @staticmethod
    def draw_overlay(frame, detections, levels):
        return frame.copy()


class Recorder:
    def __init__(self):
        self.triggers = []
        self.frames = []

    def trigger(self, incident_id, timestamp, overlay_enabled):
        self.triggers.append((incident_id, timestamp, overlay_enabled))

    def add_frame(self, frame, timestamp):
        self.frames.append(timestamp)

    def poll(self, timestamp):
        pass

    def stop(self):
        pass

    def status(self):
        return {"active_recordings": len(self.triggers), "clips_written": 0,
                "last_clip_error": None, "buffered_frames": len(self.frames)}


class PPEComplianceServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Database(Path(self.temp.name) / "events.db")
        database.initialize()
        self.incidents = IncidentRepository(database)
        self.settings = DetectorSettingsRepository(database)
        self.settings.save("CAM 02", "ppe_compliance", True, True)
        person_model = FakePersonModel([[2, 2, 18, 18]], [4])
        adapter = PPEComplianceDetector(
            Path("unused"), person_model=person_model,
            classifier=lambda _frame, _boxes: [[0.9, 0.1, 0.9]], smooth_frames=1,
        )
        self.detection = adapter.detect(np.zeros((20, 20, 3), dtype=np.uint8), "CAM 02", 0)[0]

    def tearDown(self):
        self.temp.cleanup()

    def test_warning_then_five_votes_produces_one_unsafe_clip(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        worker = Worker(frame)
        recorder = Recorder()
        service = PPEComplianceService(
            "CAM 02", worker, ServiceDetector([self.detection]), self.settings,
            self.incidents, recorder, process_interval_seconds=0,
        )
        for second in range(6):
            worker.latest = LatestFrame(frame, second + 1, float(second))
            self.assertTrue(service.process_latest_once(float(second)))
        self.assertEqual(len(recorder.triggers), 1)
        self.assertEqual(self.incidents.list()[0]["current_level"], "UNSAFE")
        self.assertEqual(recorder.triggers[0][1], 5.0)


class PPEApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        base = Settings.from_environment()
        settings = replace(base, data_dir=root, database_path=root / "events.db", clips_dir=root / "clips")
        self.client = create_app(settings).test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_settings_round_trip_and_status(self):
        response = self.client.put(
            "/api/cameras/CAM%2002/detectors/ppe",
            json={"enabled": True, "overlay_enabled": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["enabled"])
        status = self.client.get("/api/cameras/CAM%2002/detectors/ppe/status").get_json()
        self.assertEqual(status["required_items"], ["coat", "mask", "gloves"])
        self.assertFalse(status["model_loaded"])

    def test_invalid_payload_is_rejected(self):
        response = self.client.put(
            "/api/cameras/CAM%2002/detectors/ppe", json={"enabled": "yes"}
        )
        self.assertEqual(response.status_code, 400)
