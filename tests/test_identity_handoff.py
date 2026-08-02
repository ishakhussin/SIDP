import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sentrylab.database import Database, IncidentRepository
from sentrylab.domain.detection import DetectionObservation, SafetyLevel, SubjectKind
from sentrylab.services.incident_processor import IncidentVoteProcessor


def person(subject_id, timestamp, box=(10, 10, 50, 90), level=SafetyLevel.WARNING):
    return SimpleNamespace(observation=DetectionObservation(
        camera_id="CAM 02",
        detector="ppe_compliance",
        subject_id=subject_id,
        subject_kind=SubjectKind.PERSON,
        level=level,
        timestamp=float(timestamp),
        box=box,
    ))


def pair(subject_id, timestamp, boxes, level=SafetyLevel.WARNING):
    return SimpleNamespace(observation=DetectionObservation(
        camera_id="CAM 02",
        detector="unsafe_proximity",
        subject_id=subject_id,
        subject_kind=SubjectKind.PAIR,
        level=level,
        timestamp=float(timestamp),
        metadata={"boxes": boxes},
    ))


class IdentityHandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Database(Path(self.temp.name) / "events.db")
        database.initialize()
        self.repository = IncidentRepository(database)
        self.processor = IncidentVoteProcessor(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def test_changed_person_id_inherits_voter_and_incident(self):
        for second in range(3):
            self.processor.process([person("track-10", second)], second)
        for second in range(3, 6):
            levels = self.processor.process([person("track-77", second)], second)

        self.assertEqual(levels["track-77"], SafetyLevel.UNSAFE)
        incidents = self.repository.list()
        self.assertEqual(len(incidents), 1)
        detail = self.repository.get(incidents[0]["id"])
        self.assertEqual(len(detail["subjects"]), 1)
        self.assertEqual(detail["subjects"][0]["subject_id"], "track-10")
        self.assertEqual(self.processor.take_new_unsafe_incident_ids(), [incidents[0]["id"]])

    def test_far_away_person_does_not_steal_existing_identity(self):
        self.processor.process([person("track-10", 0)], 0)
        levels = self.processor.process([
            person("track-10", 1),
            person("track-77", 1, box=(200, 10, 250, 90)),
        ], 1)
        self.assertEqual(set(levels), {"track-10", "track-77"})
        incident = self.repository.get(self.repository.list()[0]["id"])
        self.assertEqual(len(incident["subjects"]), 2)

    def test_changed_pair_ids_match_unordered_person_boxes(self):
        old_boxes = [[10, 10, 50, 90], [80, 10, 120, 90]]
        new_boxes = [[82, 11, 122, 91], [12, 10, 52, 90]]
        for second in range(3):
            self.processor.process([
                pair("person-1|person-2", second, old_boxes)
            ], second)
        for second in range(3, 6):
            levels = self.processor.process([
                pair("person-8|person-9", second, new_boxes)
            ], second)

        self.assertEqual(levels["person-8|person-9"], SafetyLevel.UNSAFE)
        detail = self.repository.get(self.repository.list()[0]["id"])
        self.assertEqual(len(detail["subjects"]), 1)
        self.assertEqual(detail["subjects"][0]["subject_id"], "person-1|person-2")
