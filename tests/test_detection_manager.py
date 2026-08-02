import unittest

from sentrylab.database.zones import (
    RESTRICTED_ZONE_DETECTOR,
    UNSAFE_PROXIMITY_DETECTOR,
)
from sentrylab.services.detection_manager import DetectionManager


class BrokenOverlay:
    def render(self, frame):
        raise RuntimeError("overlay exploded")


class WorkingOverlay:
    def __init__(self):
        self.calls = 0

    def render(self, frame):
        self.calls += 1
        return frame + " + working"


class StoppableService:
    def __init__(self):
        self.close_reason = None
        self.stopped = False

    def close_active(self, reason):
        self.close_reason = reason

    def stop(self):
        self.stopped = True


class DetectionManagerIsolationTest(unittest.TestCase):
    def test_one_overlay_failure_does_not_break_later_detectors_or_stream(self):
        manager = DetectionManager(None, None, None, None, None, None)
        working = WorkingOverlay()
        manager._services[("CAM 02", RESTRICTED_ZONE_DETECTOR)] = BrokenOverlay()
        manager._services[("CAM 02", UNSAFE_PROXIMITY_DETECTOR)] = working

        output = manager.render("CAM 02", "raw")

        self.assertEqual(output, "raw + working")
        self.assertEqual(working.calls, 1)
        self.assertEqual(
            manager._render_errors[("CAM 02", RESTRICTED_ZONE_DETECTOR)],
            "overlay exploded",
        )

    def test_stop_camera_removes_only_that_cameras_detector_services(self):
        manager = DetectionManager(None, None, None, None, None, None)
        cam2 = StoppableService()
        cam1 = StoppableService()
        manager._services[("CAM 02", RESTRICTED_ZONE_DETECTOR)] = cam2
        manager._services[("CAM 01", RESTRICTED_ZONE_DETECTOR)] = cam1

        manager.stop_camera("CAM 02")

        self.assertTrue(cam2.stopped)
        self.assertEqual(cam2.close_reason, "camera switched off")
        self.assertNotIn(("CAM 02", RESTRICTED_ZONE_DETECTOR), manager._services)
        self.assertIn(("CAM 01", RESTRICTED_ZONE_DETECTOR), manager._services)
        self.assertFalse(cam1.stopped)
