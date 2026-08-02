import unittest

from sentrylab.services.monitoring_heartbeat import MonitoringHeartbeatService


class FakeCameraManager:
    def __init__(self, state="ONLINE"):
        self.state = state

    def statuses(self):
        return [{"camera_id": "CAM 02", "state": self.state}]


class FakeDetectionManager:
    def __init__(self, people_count=0, levels=None, healthy=True):
        self.people_count = people_count
        self.levels = levels or {}
        self.healthy = healthy

    def status(self, camera_id, detector):
        enabled = detector == "restricted_zone"
        return {
            "enabled": enabled,
            "running": enabled and self.healthy,
            "last_processed_at": 1.0 if enabled and self.healthy else None,
            "last_error": None,
            "people_count": self.people_count,
            "levels": self.levels,
        }


class FakeRepository:
    def __init__(self):
        self.rows = []

    def add_safe(self, camera_id, message, people_count, timestamp):
        row = {
            "camera_id": camera_id,
            "level": "SAFE",
            "message": message,
            "people_count": people_count,
            "created_at": timestamp,
        }
        self.rows.append(row)
        return row


class MonitoringHeartbeatTest(unittest.TestCase):
    def service(self, **detector_options):
        repository = FakeRepository()
        service = MonitoringHeartbeatService(
            FakeCameraManager(),
            FakeDetectionManager(**detector_options),
            repository,
            interval_seconds=300,
        )
        return service, repository

    def test_safe_heartbeat_is_written_once_every_five_minutes(self):
        service, repository = self.service(people_count=1)
        self.assertEqual(service.collect_once(0), [])
        self.assertEqual(service.collect_once(299), [])
        created = service.collect_once(300)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["message"], "Person monitored, no violation")
        self.assertEqual(service.collect_once(599), [])
        self.assertEqual(len(service.collect_once(600)), 1)
        self.assertEqual(len(repository.rows), 2)

    def test_no_activity_message_is_concise(self):
        service, _repository = self.service(people_count=0)
        service.collect_once(0)
        created = service.collect_once(300)
        self.assertEqual(created[0]["message"], "No activity detected")

    def test_warning_or_unsafe_prevents_safe_heartbeat(self):
        service, repository = self.service(levels={"person-1": "WARNING"})
        service.collect_once(0)
        self.assertEqual(service.collect_once(300), [])
        self.assertEqual(repository.rows, [])

    def test_unhealthy_monitoring_never_claims_safe(self):
        service, repository = self.service(healthy=False)
        service.collect_once(0)
        self.assertEqual(service.collect_once(300), [])
        self.assertEqual(repository.rows, [])


if __name__ == "__main__":
    unittest.main()
