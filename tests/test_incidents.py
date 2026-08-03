import io
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from sentrylab import create_app
from sentrylab.config import Settings
from sentrylab.database import Database, IncidentRepository
from sentrylab.domain.detection import (
    DetectionObservation,
    SafetyLevel,
    SubjectKind,
)


def observation(
    subject_id,
    level,
    timestamp,
    camera_id="CAM 01",
    detector="ppe",
    kind=SubjectKind.PERSON,
    **metadata,
):
    return DetectionObservation(
        camera_id=camera_id,
        detector=detector,
        subject_id=subject_id,
        subject_kind=kind,
        level=level,
        timestamp=timestamp,
        metadata=metadata,
    )


class IncidentRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Database(Path(self.temp.name) / "events.db")
        database.initialize()
        self.repository = IncidentRepository(database)

    def tearDown(self):
        self.temp.cleanup()

    def test_people_join_one_camera_detector_incident(self):
        first = self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 1.0)
        )
        unsafe = self.repository.record_observation(
            observation("person-a", SafetyLevel.UNSAFE, 3.0)
        )
        second_person = self.repository.record_observation(
            observation("person-b", SafetyLevel.WARNING, 4.0)
        )

        self.assertEqual(first["id"], unsafe["id"])
        self.assertEqual(first["id"], second_person["id"])
        detail = self.repository.get(first["id"])
        self.assertEqual(len(detail["subjects"]), 2)
        self.assertEqual(detail["current_level"], "UNSAFE")

    def test_incident_closes_only_after_every_subject_recovers(self):
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.UNSAFE, 1.0)
        )
        self.repository.record_observation(
            observation("person-b", SafetyLevel.WARNING, 2.0)
        )
        after_a = self.repository.record_observation(
            observation("person-a", SafetyLevel.SAFE, 3.0)
        )
        self.assertEqual(after_a["current_level"], "WARNING")

        closed = self.repository.record_observation(
            observation(
                "person-b",
                SafetyLevel.SAFE,
                4.0,
                close_reason="subject left frame",
            )
        )
        self.assertEqual(closed["current_level"], "CLOSED")
        self.assertEqual(closed["close_reason"], "subject left frame")

        next_incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 10.0)
        )
        self.assertNotEqual(next_incident["id"], incident["id"])

    def test_unknown_does_not_open_or_change_incident(self):
        self.assertIsNone(self.repository.record_observation(
            observation("person-a", SafetyLevel.UNKNOWN, 1.0)
        ))
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 2.0)
        )
        unchanged = self.repository.record_observation(
            observation("person-a", SafetyLevel.UNKNOWN, 3.0)
        )
        self.assertEqual(unchanged["id"], incident["id"])
        self.assertEqual(self.repository.get(incident["id"])["current_level"], "WARNING")

    def test_repeated_level_does_not_duplicate_transition(self):
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 1.0)
        )
        self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 2.0)
        )
        detail = self.repository.get(incident["id"])
        self.assertEqual(len(detail["transitions"]), 1)

    def test_runtime_restart_closes_stale_active_subjects(self):
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.UNSAFE, 1.0)
        )
        recovered = self.repository.close_stale_active(
            "CAM 01", "ppe", 10.0
        )
        self.assertEqual(recovered["current_level"], "CLOSED")
        self.assertEqual(recovered["close_reason"], "runtime restarted")
        detail = self.repository.get(incident["id"])
        self.assertEqual(detail["subjects"][0]["current_level"], "SAFE")
        self.assertEqual(detail["transitions"][-1]["from_level"], "UNSAFE")
        self.assertEqual(detail["transitions"][-1]["to_level"], "SAFE")


class IncidentApiTest(unittest.TestCase):
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
        settings.clips_dir.mkdir()
        self.app = create_app(settings)
        self.client = self.app.test_client()
        self.repository = self.app.extensions["incident_repository"]

    def tearDown(self):
        self.temp.cleanup()

    def _closed_incident_with_clip(self):
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.UNSAFE, 1.0)
        )
        self.repository.record_observation(
            observation("person-a", SafetyLevel.SAFE, 2.0)
        )
        clip_name = f"incident-{incident['id']}.mp4"
        clip = self.app.config["SENTRYLAB_SETTINGS"].clips_dir / clip_name
        clip.write_bytes(b"video")
        self.repository.set_clip(incident["id"], clip_name, True)
        return incident["id"], clip

    def test_list_and_detail(self):
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 1.0)
        )
        listing = self.client.get("/api/incidents").get_json()
        detail = self.client.get(f"/api/incidents/{incident['id']}")
        self.assertEqual(listing["count"], 1)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.get_json()["subjects"]), 1)

        summary = self.client.get(
            "/api/dashboard/summary?camera_id=CAM%2001"
        ).get_json()
        self.assertEqual(summary["total_events"], 1)
        self.assertEqual(summary["safe"], 0)
        self.assertEqual(summary["warnings"], 1)
        self.assertEqual(summary["unsafe"], 0)

    def test_combined_log_contains_safe_warning_unsafe_and_recovery(self):
        monitoring = self.app.extensions["monitoring_log_repository"]
        monitoring.add_safe("CAM 01", "No activity detected", 0, 1.0)
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 2.0)
        )
        self.repository.record_observation(
            observation("person-a", SafetyLevel.UNSAFE, 3.0)
        )
        self.repository.record_observation(
            observation("person-a", SafetyLevel.SAFE, 4.0)
        )

        response = self.client.get("/api/log-entries?camera_id=CAM%2001")
        self.assertEqual(response.status_code, 200)
        entries = response.get_json()["entries"]
        self.assertEqual(
            [entry["level"] for entry in entries],
            ["SAFE", "UNSAFE", "WARNING", "SAFE"],
        )
        self.assertEqual(entries[0]["message"], "Returned to a safe condition")
        self.assertEqual(entries[-1]["message"], "No activity detected")
        self.assertTrue(all(
            entry["incident_id"] == incident["id"]
            for entry in entries[:-1]
        ))

        summary = self.client.get(
            "/api/dashboard/summary?camera_id=CAM%2001"
        ).get_json()
        self.assertEqual(summary["safe"], 2)
        self.assertEqual(summary["warnings"], 1)
        self.assertEqual(summary["unsafe"], 1)
        self.assertEqual(summary["total_events"], 4)

        export = self.client.get("/api/log-entries/export.csv")
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"No activity detected", export.data)
        self.assertIn(b"WARNING", export.data)

    def test_active_incident_cannot_be_deleted(self):
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.WARNING, 1.0)
        )
        response = self.client.delete(f"/api/incidents/{incident['id']}")
        self.assertEqual(response.status_code, 409)

    def test_delete_closed_incident_removes_clip(self):
        incident_id, clip = self._closed_incident_with_clip()
        response = self.client.delete(f"/api/incidents/{incident_id}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["clip_deleted"])
        self.assertFalse(clip.exists())
        self.assertIsNone(self.repository.get(incident_id))

    def test_clip_can_be_played_inline(self):
        incident_id, _clip = self._closed_incident_with_clip()
        response = self.client.get(f"/api/incidents/{incident_id}/clip")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "video/mp4")
            self.assertEqual(response.data, b"video")
        finally:
            response.close()

    def test_h264_clip_can_be_played_in_browser_without_conversion(self):
        incident_id, clip = self._closed_incident_with_clip()
        h264_clip = clip.with_name(f"{clip.stem}-h264.mp4")
        clip.replace(h264_clip)
        self.repository.set_clip(incident_id, h264_clip.name, True)
        response = self.client.get(f"/api/incidents/{incident_id}/browser-clip")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "video/mp4")
            self.assertEqual(response.data, b"video")
        finally:
            response.close()

    def test_missing_clip_returns_not_ready(self):
        incident = self.repository.record_observation(
            observation("person-a", SafetyLevel.UNSAFE, 1.0)
        )
        response = self.client.get(f"/api/incidents/{incident['id']}/clip")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Evidence clip is not ready")

    def test_csv_and_zip_export(self):
        incident_id, _clip = self._closed_incident_with_clip()
        csv_response = self.client.get("/api/incidents/export.csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn(b"camera_id", csv_response.data)
        self.assertIn(b"CAM 01", csv_response.data)

        zip_response = self.client.get(
            f"/api/incidents/export.zip?ids={incident_id}"
        )
        self.assertEqual(zip_response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(zip_response.data)) as archive:
            self.assertIn("incidents.csv", archive.namelist())
            self.assertTrue(any(name.startswith("clips/") for name in archive.namelist()))


if __name__ == "__main__":
    unittest.main()
