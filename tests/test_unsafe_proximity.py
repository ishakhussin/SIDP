import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from sentrylab import create_app
from sentrylab.config import Settings
from sentrylab.detection.unsafe_proximity import (
    DepthResult,
    PersonTrack,
    UnsafeProximityDetector,
)
from sentrylab.domain.detection import SafetyLevel, SubjectKind


class UnsafeProximityDetectorTest(unittest.TestCase):
    def setUp(self):
        self.detector = UnsafeProximityDetector(
            Path("unused"),
            person_model_factory=lambda _path: object(),
            depth_components_factory=lambda _path: (object(), object()),
        )

    def test_metric_pair_distance_and_stable_pair_identity(self):
        depth = DepthResult(np.full((100, 100), 2.0), 100.0, 10.0)
        people = [
            PersonTrack(9, (65, 10, 85, 90), 0.80),
            PersonTrack(3, (15, 10, 35, 90), 0.90),
        ]
        result = self.detector.measure(
            people, depth, 100, "CAM 02", 11.0
        )[0]
        self.assertAlmostEqual(result.distance_metres, 1.0, places=5)
        self.assertEqual(result.observation.level, SafetyLevel.WARNING)
        self.assertEqual(result.observation.subject_id, "person-3|person-9")
        self.assertEqual(result.observation.subject_kind, SubjectKind.PAIR)

    def test_expired_depth_is_unknown_and_has_no_distance(self):
        depth = DepthResult(np.full((100, 100), 2.0), 100.0, 1.0)
        people = [
            PersonTrack(1, (10, 10, 30, 90), 0.9),
            PersonTrack(2, (60, 10, 80, 90), 0.9),
        ]
        result = self.detector.measure(
            people, depth, 100, "CAM 02", 7.0, maximum_depth_age=5.0
        )[0]
        self.assertEqual(result.observation.level, SafetyLevel.UNKNOWN)
        self.assertIsNone(result.distance_metres)

    def test_safe_pair_is_at_or_above_one_point_five_metres(self):
        depth = DepthResult(np.full((100, 100), 3.0), 100.0, 10.0)
        people = [
            PersonTrack(1, (10, 10, 30, 90), 0.9),
            PersonTrack(2, (70, 10, 90, 90), 0.9),
        ]
        result = self.detector.measure(people, depth, 100, "CAM 02", 10.0)[0]
        self.assertAlmostEqual(result.distance_metres, 1.8, places=5)
        self.assertEqual(result.observation.level, SafetyLevel.SAFE)


class UnsafeProximityApiTest(unittest.TestCase):
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
        self.app = create_app(settings)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_settings_round_trip_without_loading_models(self):
        response = self.client.put(
            "/api/cameras/CAM%2002/detectors/unsafe-proximity",
            json={"enabled": True, "overlay_enabled": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["enabled"])
        status = self.client.get(
            "/api/cameras/CAM%2002/detectors/unsafe-proximity/status"
        ).get_json()
        self.assertFalse(status["running"])
        self.assertFalse(status["person_model_loaded"])
        self.assertFalse(status["depth_model_loaded"])

    def test_settings_require_booleans(self):
        response = self.client.put(
            "/api/cameras/CAM%2002/detectors/unsafe-proximity",
            json={"enabled": "yes", "overlay_enabled": True},
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_camera_is_rejected(self):
        response = self.client.get(
            "/api/cameras/CAM%2099/detectors/unsafe-proximity"
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
