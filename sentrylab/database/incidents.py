"""Atomic incident grouping and query repository."""

from __future__ import annotations

import json
from typing import Any

from sentrylab.database.connection import Database
from sentrylab.domain.detection import DetectionObservation, SafetyLevel


ACTIVE_LEVELS = (SafetyLevel.WARNING.value, SafetyLevel.UNSAFE.value)


def _row_dict(row) -> dict | None:
    return dict(row) if row is not None else None


class IncidentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _active_incident(connection, camera_id: str, detector: str):
        return connection.execute(
            "SELECT * FROM incidents WHERE camera_id = ? AND detector = ? "
            "AND closed_at IS NULL",
            (camera_id, detector),
        ).fetchone()

    @staticmethod
    def _create_incident(connection, observation: DetectionObservation):
        level = (
            SafetyLevel.UNSAFE.value
            if observation.level is SafetyLevel.UNSAFE
            else SafetyLevel.WARNING.value
        )
        unsafe_at = observation.timestamp if level == SafetyLevel.UNSAFE.value else None
        cursor = connection.execute(
            "INSERT INTO incidents "
            "(camera_id, detector, current_level, opened_at, unsafe_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                observation.camera_id,
                observation.detector,
                level,
                observation.timestamp,
                unsafe_at,
                observation.timestamp,
            ),
        )
        return connection.execute(
            "SELECT * FROM incidents WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    @staticmethod
    def _recalculate_incident(connection, incident_id: int, timestamp: float) -> None:
        counts = connection.execute(
            "SELECT "
            "SUM(CASE WHEN current_level = 'UNSAFE' THEN 1 ELSE 0 END) unsafe_count, "
            "SUM(CASE WHEN current_level = 'WARNING' THEN 1 ELSE 0 END) warning_count "
            "FROM incident_subjects WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        unsafe_count = int(counts["unsafe_count"] or 0)
        warning_count = int(counts["warning_count"] or 0)

        if unsafe_count:
            connection.execute(
                "UPDATE incidents SET current_level = 'UNSAFE', "
                "unsafe_at = COALESCE(unsafe_at, ?), updated_at = ? WHERE id = ?",
                (timestamp, timestamp, incident_id),
            )
        elif warning_count:
            connection.execute(
                "UPDATE incidents SET current_level = 'WARNING', updated_at = ? "
                "WHERE id = ?",
                (timestamp, incident_id),
            )
        else:
            connection.execute(
                "UPDATE incidents SET current_level = 'CLOSED', closed_at = ?, "
                "close_reason = COALESCE(close_reason, 'all subjects recovered'), "
                "updated_at = ? WHERE id = ?",
                (timestamp, timestamp, incident_id),
            )

    def record_observation(self, observation: DetectionObservation) -> dict | None:
        """Group a confirmed subject level into one active camera incident."""
        with self.database.transaction() as connection:
            incident = self._active_incident(
                connection, observation.camera_id, observation.detector
            )

            if incident is None and observation.level in {
                SafetyLevel.SAFE,
                SafetyLevel.UNKNOWN,
            }:
                return None
            if incident is None:
                incident = self._create_incident(connection, observation)

            incident_id = int(incident["id"])
            subject = connection.execute(
                "SELECT * FROM incident_subjects WHERE incident_id = ? "
                "AND subject_kind = ? AND subject_id = ?",
                (
                    incident_id,
                    observation.subject_kind.value,
                    observation.subject_id,
                ),
            ).fetchone()

            previous_level = (
                subject["current_level"] if subject is not None else SafetyLevel.SAFE.value
            )
            details = dict(observation.metadata)
            if observation.confidence is not None:
                details["confidence"] = observation.confidence
            if observation.box is not None:
                details["box"] = list(observation.box)
            details_json = json.dumps(details, separators=(",", ":"), sort_keys=True)

            if observation.level is SafetyLevel.UNKNOWN:
                if subject is not None:
                    connection.execute(
                        "UPDATE incident_subjects SET last_seen_at = ?, details_json = ? "
                        "WHERE incident_id = ? AND subject_kind = ? AND subject_id = ?",
                        (
                            observation.timestamp,
                            details_json,
                            incident_id,
                            observation.subject_kind.value,
                            observation.subject_id,
                        ),
                    )
                return _row_dict(incident)

            connection.execute(
                "INSERT INTO incident_subjects "
                "(incident_id, subject_id, subject_kind, current_level, "
                "first_seen_at, last_seen_at, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(incident_id, subject_kind, subject_id) DO UPDATE SET "
                "current_level = excluded.current_level, "
                "last_seen_at = excluded.last_seen_at, details_json = excluded.details_json",
                (
                    incident_id,
                    observation.subject_id,
                    observation.subject_kind.value,
                    observation.level.value,
                    observation.timestamp,
                    observation.timestamp,
                    details_json,
                ),
            )

            if (
                observation.level is SafetyLevel.SAFE
                and observation.metadata.get("close_reason")
            ):
                connection.execute(
                    "UPDATE incidents SET close_reason = ? WHERE id = ?",
                    (str(observation.metadata["close_reason"]), incident_id),
                )

            if previous_level != observation.level.value:
                connection.execute(
                    "INSERT INTO incident_transitions "
                    "(incident_id, camera_id, detector, subject_id, subject_kind, "
                    "from_level, to_level, occurred_at, details_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        incident_id,
                        observation.camera_id,
                        observation.detector,
                        observation.subject_id,
                        observation.subject_kind.value,
                        previous_level,
                        observation.level.value,
                        observation.timestamp,
                        details_json,
                    ),
                )

            self._recalculate_incident(connection, incident_id, observation.timestamp)
            return _row_dict(connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone())

    def set_clip(self, incident_id: int, clip_path: str, overlay_enabled: bool) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE incidents SET clip_path = ?, overlay_enabled = ? WHERE id = ?",
                (clip_path, int(overlay_enabled), incident_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown incident: {incident_id}")

    def close_stale_active(
        self,
        camera_id: str,
        detector: str,
        timestamp: float,
        reason: str = "runtime restarted",
    ) -> dict | None:
        """Recover an incident left active by an interrupted prior process."""
        with self.database.transaction() as connection:
            incident = self._active_incident(connection, camera_id, detector)
            if incident is None:
                return None
            incident_id = int(incident["id"])
            subjects = connection.execute(
                "SELECT * FROM incident_subjects WHERE incident_id = ? "
                "AND current_level IN ('WARNING', 'UNSAFE')",
                (incident_id,),
            ).fetchall()
            for subject in subjects:
                try:
                    details = json.loads(subject["details_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    details = {}
                details["close_reason"] = reason
                details_json = json.dumps(
                    details, separators=(",", ":"), sort_keys=True
                )
                connection.execute(
                    "UPDATE incident_subjects SET current_level = 'SAFE', "
                    "last_seen_at = ?, details_json = ? WHERE incident_id = ? "
                    "AND subject_kind = ? AND subject_id = ?",
                    (
                        timestamp,
                        details_json,
                        incident_id,
                        subject["subject_kind"],
                        subject["subject_id"],
                    ),
                )
                connection.execute(
                    "INSERT INTO incident_transitions "
                    "(incident_id, camera_id, detector, subject_id, subject_kind, "
                    "from_level, to_level, occurred_at, details_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'SAFE', ?, ?)",
                    (
                        incident_id,
                        camera_id,
                        detector,
                        subject["subject_id"],
                        subject["subject_kind"],
                        subject["current_level"],
                        timestamp,
                        details_json,
                    ),
                )
            connection.execute(
                "UPDATE incidents SET close_reason = ? WHERE id = ?",
                (reason, incident_id),
            )
            self._recalculate_incident(connection, incident_id, timestamp)
            return _row_dict(connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone())

    def list(
        self,
        camera_id: str | None = None,
        detector: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        clauses, parameters = [], []
        if camera_id:
            clauses.append("camera_id = ?")
            parameters.append(camera_id)
        if detector:
            clauses.append("detector = ?")
            parameters.append(detector)
        if status:
            clauses.append("current_level = ?")
            parameters.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        with self.database.session() as connection:
            rows = connection.execute(
                f"SELECT * FROM incidents {where} ORDER BY opened_at DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, camera_id: str | None = None, recent_limit: int = 5) -> dict:
        where = "WHERE camera_id = ?" if camera_id else ""
        parameters = [camera_id] if camera_id else []
        with self.database.session() as connection:
            counts = connection.execute(
                f"SELECT COUNT(*) total, "
                "SUM(CASE WHEN unsafe_at IS NULL THEN 1 ELSE 0 END) warnings, "
                "SUM(CASE WHEN unsafe_at IS NOT NULL THEN 1 ELSE 0 END) unsafe, "
                "SUM(CASE WHEN current_level = 'CLOSED' THEN 1 ELSE 0 END) closed "
                f"FROM incidents {where}",
                parameters,
            ).fetchone()
            recent = connection.execute(
                f"SELECT * FROM incidents {where} ORDER BY opened_at DESC LIMIT ?",
                parameters + [max(1, min(int(recent_limit), 50))],
            ).fetchall()
        return {
            "total_events": int(counts["total"] or 0),
            "warnings": int(counts["warnings"] or 0),
            "unsafe": int(counts["unsafe"] or 0),
            "closed": int(counts["closed"] or 0),
            # Incidents contain violations only; a true SAFE percentage will
            # require a monitoring-uptime aggregate in a later stage.
            "safe_pct": None,
            "recent_incidents": [dict(row) for row in recent],
        }

    def get(self, incident_id: int) -> dict | None:
        with self.database.session() as connection:
            incident = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if incident is None:
                return None
            subjects = connection.execute(
                "SELECT * FROM incident_subjects WHERE incident_id = ? "
                "ORDER BY first_seen_at, subject_id",
                (incident_id,),
            ).fetchall()
            transitions = connection.execute(
                "SELECT * FROM incident_transitions WHERE incident_id = ? "
                "ORDER BY occurred_at, id",
                (incident_id,),
            ).fetchall()
        output = dict(incident)
        output["subjects"] = [dict(row) for row in subjects]
        output["transitions"] = [dict(row) for row in transitions]
        return output

    def delete(self, incident_id: int) -> dict | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
            return dict(row)
