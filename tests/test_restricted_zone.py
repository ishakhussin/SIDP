import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from sentrylab import create_app
from sentrylab.config import Settings
from sentrylab.cameras.base import LatestFrame
from sentrylab.database import (
    Database,
    DetectorSettingsRepository,
    IncidentRepository,
    ZoneRepository,
)
from sentrylab.detection.restricted_zone import (
    RestrictedZoneDetection,
    RestrictedZoneDetector,
    point_in_polygon,
)
from sentrylab.domain.detection import (
    DetectionObservation,
    SafetyLevel,
    SubjectKind,
)
from sentrylab.services.restricted_zone_processor import RestrictedZoneProcessor
from sentrylab.services.restricted_zone_service import RestrictedZoneService


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


class FakeKeypoints:
    def __init__(self, coordinates, confidence):
        self.xy = FakeTensor(coordinates)
        self.conf = FakeTensor(confidence)


class FakePoseResult:
    def __init__(self, boxes, ids, coordinates, confidence):
        self.boxes = FakeBoxes(boxes, ids)
        self.keypoints = FakeKeypoints(coordinates, confidence)


class FakePoseModel:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def track(self, frame, **kwargs):
        self.calls.append(kwargs)
        return [self.result]


def pose_person(box, left_ankle, right_ankle, left_conf=0.9, right_conf=0.9):
    coordinates = np.zeros((17, 2), dtype=float)
    confidence = np.zeros(17, dtype=float)
    coordinates[15], coordinates[16] = left_ankle, right_ankle
    confidence[15], confidence[16] = left_conf, right_conf
    return box, coordinates, confidence


class RestrictedZoneDetectorTest(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.zone = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]]

    def _detector(self, people):
        boxes, coordinates, confidences = zip(*people)
        result = FakePoseResult(
            boxes,
            list(range(1, len(people) + 1)),
            coordinates,
            confidences,
        )
        model = FakePoseModel(result)
        return RestrictedZoneDetector(
            Path("unused.pt"), model_factory=lambda _path: model
        ), model

    def test_either_ankle_inside_is_warning(self):
        detector, _model = self._detector([
            pose_person([10, 10, 90, 90], [50, 50], [10, 10])
        ])
        detection = detector.detect(self.frame, self.zone, "CAM 01", 1.0)[0]
        self.assertEqual(detection.observation.level, SafetyLevel.WARNING)
        self.assertEqual(detection.points_inside, (True, False))
        self.assertFalse(detection.used_fallback)

    def test_both_low_confidence_use_box_bottom_center(self):
        detector, _model = self._detector([
            pose_person(
                [30, 10, 70, 60],
                [0, 0],
                [0, 0],
                left_conf=0.1,
                right_conf=0.1,
            )
        ])
        detection = detector.detect(self.frame, self.zone, "CAM 01", 1.0)[0]
        self.assertTrue(detection.used_fallback)
        self.assertEqual(detection.check_points, ((50.0, 60.0),))
        self.assertEqual(detection.observation.level, SafetyLevel.WARNING)

    def test_polygon_boundary_counts_as_inside(self):
        self.assertTrue(point_in_polygon((0.25, 0.5), self.zone))
        self.assertFalse(point_in_polygon((0.1, 0.5), self.zone))


def raw_detection(subject_id: str, level: SafetyLevel, timestamp: float):
    observation = DetectionObservation(
        camera_id="CAM 01",
        detector="restricted_zone",
        subject_id=subject_id,
        subject_kind=SubjectKind.PERSON,
        level=level,
        timestamp=timestamp,
        box=(1, 1, 10, 10),
    )
    return RestrictedZoneDetection(observation, ((5.0, 10.0),), (level is SafetyLevel.WARNING,), False)


class RestrictedZoneProcessorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Database(Path(self.temp.name) / "events.db")
        database.initialize()
        self.repository = IncidentRepository(database)
        self.processor = RestrictedZoneProcessor(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def test_warning_is_immediate_but_unsafe_waits_for_five_votes(self):
        confirmed = self.processor.process(
            [raw_detection("person-a", SafetyLevel.WARNING, 0)], 0
        )
        self.assertEqual(confirmed["person-a"], SafetyLevel.WARNING)
        levels = [
            SafetyLevel.WARNING,
            SafetyLevel.WARNING,
            SafetyLevel.WARNING,
            SafetyLevel.SAFE,
            SafetyLevel.SAFE,
        ]
        for second, level in enumerate(levels, start=1):
            confirmed = self.processor.process(
                [raw_detection("person-a", level, second)], second
            )
            if second < 5:
                self.assertNotEqual(confirmed["person-a"], SafetyLevel.UNSAFE)
        self.assertEqual(confirmed["person-a"], SafetyLevel.UNSAFE)
        incidents = self.repository.list()
        self.assertEqual(incidents[0]["current_level"], "UNSAFE")
        self.assertEqual(
            self.processor.take_new_unsafe_incident_ids(), [incidents[0]["id"]]
        )
        self.assertEqual(self.processor.take_new_unsafe_incident_ids(), [])

    def test_people_vote_independently_and_share_incident(self):
        for second in range(5):
            detections = [raw_detection("person-a", SafetyLevel.WARNING, second)]
            if second >= 2:
                detections.append(raw_detection("person-b", SafetyLevel.WARNING, second))
            self.processor.process(detections, second)
        levels = self.processor.process(
            [
                raw_detection("person-a", SafetyLevel.WARNING, 5),
                raw_detection("person-b", SafetyLevel.WARNING, 5),
            ],
            5,
        )
        self.assertEqual(levels["person-a"], SafetyLevel.UNSAFE)
        self.assertEqual(levels["person-b"], SafetyLevel.WARNING)
        self.assertEqual(len(self.repository.list()), 1)

    def test_missing_track_expires_after_two_seconds(self):
        self.processor.process(
            [raw_detection("person-a", SafetyLevel.WARNING, 0)], 0
        )
        self.processor.process([], 2.01)
        incident = self.repository.get(self.repository.list()[0]["id"])
        self.assertEqual(incident["current_level"], "CLOSED")
        self.assertEqual(incident["close_reason"], "subject left frame")


class SingleFrameWorker:
    def __init__(self, frame, sequence=1, captured_at=1.0):
        self.latest = LatestFrame(frame, sequence, captured_at)

    def get_latest_frame(self, copy=True):
        return self.latest


class ServiceDetector:
    def __init__(self, detections):
        self.detections = detections
        self.calls = 0
        self.loaded = False
        self.overlay_calls = 0

    def detect(self, frame, zone, camera_id, timestamp):
        self.calls += 1
        self.loaded = True
        return self.detections

    def draw_overlay(self, frame, zone, detections, levels):
        self.overlay_calls += 1
        return frame.copy()


class ServiceRecorder:
    def __init__(self):
        self.frames = []
        self.triggers = []

    def add_frame(self, frame, timestamp):
        self.frames.append((frame.copy(), timestamp))

    def trigger(self, incident_id, timestamp, overlay_enabled):
        self.triggers.append((incident_id, timestamp, overlay_enabled))

    def poll(self, timestamp):
        return None

    def stop(self):
        return None

    def status(self):
        return {
            "active_recordings": len(self.triggers),
            "clips_written": 0,
            "last_clip_error": None,
            "buffered_frames": len(self.frames),
        }

    def record_error(self, error):
        raise AssertionError(str(error))


class RestrictedZoneServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Database(Path(self.temp.name) / "events.db")
        database.initialize()
        self.incidents = IncidentRepository(database)
        self.zones = ZoneRepository(database)
        self.settings = DetectorSettingsRepository(database)
        self.zones.save(
            "CAM 01",
            "HOME",
            [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        )
        self.settings.save("CAM 01", "restricted_zone", True, True)

    def tearDown(self):
        self.temp.cleanup()

    def test_service_processes_each_camera_sequence_once(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        detector = ServiceDetector([
            raw_detection("person-a", SafetyLevel.WARNING, 1.0)
        ])
        service = RestrictedZoneService(
            "CAM 01",
            SingleFrameWorker(frame),
            detector,
            self.zones,
            self.settings,
            self.incidents,
            ServiceRecorder(),
        )
        self.assertTrue(service.process_latest_once(1.0))
        self.assertFalse(service.process_latest_once(1.1))
        self.assertEqual(detector.calls, 1)
        self.assertEqual(self.incidents.list()[0]["current_level"], "WARNING")

        rendered = service.render(frame)
        self.assertEqual(rendered.shape, frame.shape)
        self.assertEqual(detector.overlay_calls, 2)

    def test_disabled_service_does_not_load_detector(self):
        self.settings.save("CAM 01", "restricted_zone", False, True)
        detector = ServiceDetector([])
        service = RestrictedZoneService(
            "CAM 01",
            SingleFrameWorker(np.zeros((10, 10, 3), dtype=np.uint8)),
            detector,
            self.zones,
            self.settings,
            self.incidents,
            ServiceRecorder(),
        )
        self.assertFalse(service.process_latest_once(1.0))
        self.assertFalse(detector.loaded)

    def test_unsafe_starts_one_evidence_recording(self):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        worker = SingleFrameWorker(frame)
        detector = ServiceDetector([
            raw_detection("person-a", SafetyLevel.WARNING, 0.0)
        ])
        recorder = ServiceRecorder()
        service = RestrictedZoneService(
            "CAM 01",
            worker,
            detector,
            self.zones,
            self.settings,
            self.incidents,
            recorder,
        )
        for second in range(6):
            worker.latest = LatestFrame(frame, second + 1, float(second))
            self.assertTrue(service.process_latest_once(float(second)))

        self.assertEqual(len(recorder.triggers), 1)
        incident_id, timestamp, overlay_enabled = recorder.triggers[0]
        self.assertEqual(incident_id, self.incidents.list()[0]["id"])
        self.assertEqual(timestamp, 5.0)
        self.assertTrue(overlay_enabled)


class RestrictedZoneApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        base = Settings.from_environment()
        settings = replace(
            base,
            data_dir=root,
            database_path=root / "events.db",
            clips_dir=root / "clips",
        )
        self.client = create_app(settings).test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_zone_is_saved_per_camera_and_preset(self):
        points = [[0.1, 0.1], [0.8, 0.1], [0.8, 0.8], [0.1, 0.8]]
        response = self.client.put(
            "/api/cameras/CAM%2001/restricted-zone?preset=HOME",
            json={"points": points},
        )
        self.assertEqual(response.status_code, 200)
        loaded = self.client.get(
            "/api/cameras/CAM%2001/restricted-zone?preset=HOME"
        ).get_json()
        self.assertEqual(loaded["points"], points)

    def test_invalid_zone_is_rejected(self):
        response = self.client.put(
            "/api/cameras/CAM%2001/restricted-zone",
            json={"points": [[2, 2], [3, 3], [4, 4]]},
        )
        self.assertEqual(response.status_code, 400)

    def test_detector_settings_round_trip(self):
        response = self.client.put(
            "/api/cameras/CAM%2001/detectors/restricted-zone",
            json={"enabled": True, "overlay_enabled": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["enabled"])
        loaded = self.client.get(
            "/api/cameras/CAM%2001/detectors/restricted-zone"
        ).get_json()
        self.assertFalse(loaded["overlay_enabled"])

    def test_detector_settings_require_json_booleans(self):
        response = self.client.put(
            "/api/cameras/CAM%2001/detectors/restricted-zone",
            json={"enabled": "false", "overlay_enabled": True},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
