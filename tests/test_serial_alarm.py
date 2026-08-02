import unittest

from sentrylab.services.serial_alarm import SerialAlarmService


DETECTORS = ("restricted_zone", "unsafe_proximity", "ppe_compliance")


class StubCameraManager:
    def statuses(self):
        return [
            {"camera_id": "CAM 01"},
            {"camera_id": "CAM 02"},
        ]


class StubDetectionManager:
    def __init__(self):
        self.levels = {}

    def status(self, camera_id, detector):
        return {
            "enabled": True,
            "levels": self.levels.get((camera_id, detector), {}),
        }


class FakeSerial:
    def __init__(self):
        self.commands = []
        self.closed = False

    def write(self, payload):
        self.commands.append(payload.decode("ascii").strip())

    def flush(self):
        pass

    def close(self):
        self.closed = True


class SerialAlarmTest(unittest.TestCase):
    def setUp(self):
        self.detection = StubDetectionManager()
        self.serial = FakeSerial()
        self.service = SerialAlarmService(
            StubCameraManager(),
            self.detection,
            port="COM7",
            serial_factory=lambda _port, _baud: self.serial,
        )

    def test_safe_system_sends_heartbeat(self):
        command = self.service.poll_once(now=0.0)

        self.assertEqual(command, "HEARTBEAT")
        self.assertEqual(self.serial.commands, ["HEARTBEAT"])
        self.assertFalse(self.service.status()["alarm_active"])

    def test_any_confirmed_unsafe_use_case_activates_alarm(self):
        for detector in DETECTORS:
            with self.subTest(detector=detector):
                self.detection.levels = {
                    ("CAM 02", detector): {"subject": "UNSAFE"}
                }
                command = self.service.poll_once(now=1.0)
                self.assertEqual(command, f"ALARM_ON:CAM 02:{detector}")
                self.assertTrue(self.service.status()["alarm_active"])

    def test_alarm_continues_if_one_of_multiple_sources_remains_unsafe(self):
        self.detection.levels = {
            ("CAM 01", "restricted_zone"): {"person": "UNSAFE"},
            ("CAM 02", "ppe_compliance"): {"person": "UNSAFE"},
        }
        self.service.poll_once(now=0.0)
        self.detection.levels.pop(("CAM 01", "restricted_zone"))

        command = self.service.poll_once(now=1.0)

        self.assertEqual(command, "ALARM_ON:CAM 02:ppe_compliance")

    def test_alarm_clears_after_two_continuous_safe_seconds(self):
        self.detection.levels = {
            ("CAM 01", "unsafe_proximity"): {"pair": "UNSAFE"}
        }
        self.service.poll_once(now=0.0)
        self.detection.levels = {}

        self.assertTrue(self.service.poll_once(now=1.0).startswith("ALARM_ON:"))
        self.assertTrue(self.service.poll_once(now=2.9).startswith("ALARM_ON:"))
        self.assertEqual(self.service.poll_once(now=3.0), "ALARM_OFF")
        self.assertFalse(self.service.status()["alarm_active"])

    def test_serial_failure_isolated_and_reconnects_on_next_poll(self):
        attempts = []

        def factory(_port, _baud):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("port busy")
            return self.serial

        service = SerialAlarmService(
            StubCameraManager(), self.detection, "COM7", serial_factory=factory
        )
        service.poll_once(now=0.0)
        self.assertFalse(service.status()["connected"])
        self.assertIn("port busy", service.status()["last_error"])

        service.poll_once(now=1.0)

        self.assertTrue(service.status()["connected"])
        self.assertEqual(self.serial.commands, ["HEARTBEAT"])

    def test_alarm_status_api_is_available_without_a_com_port(self):
        from sentrylab import create_app

        response = create_app().test_client().get("/api/alarm/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["configured"])


if __name__ == "__main__":
    unittest.main()
