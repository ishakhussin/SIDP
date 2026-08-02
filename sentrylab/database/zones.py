"""Restricted-zone polygons and detector settings."""

from __future__ import annotations

import json
import time

from sentrylab.database.connection import Database


RESTRICTED_ZONE_DETECTOR = "restricted_zone"
UNSAFE_PROXIMITY_DETECTOR = "unsafe_proximity"
PPE_COMPLIANCE_DETECTOR = "ppe_compliance"


def validate_normalized_polygon(points) -> list[list[float]]:
    if not isinstance(points, list) or not 3 <= len(points) <= 50:
        raise ValueError("points must contain between 3 and 50 coordinates")

    clean = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("each point must contain x and y")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError) as error:
            raise ValueError("zone coordinates must be numeric") from error
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError("zone coordinates must be normalized between 0 and 1")
        clean.append([round(x, 6), round(y, 6)])

    area = abs(sum(
        clean[index][0] * clean[(index + 1) % len(clean)][1]
        - clean[(index + 1) % len(clean)][0] * clean[index][1]
        for index in range(len(clean))
    )) / 2.0
    if area < 0.0001:
        raise ValueError("zone polygon area is too small")
    return clean


class ZoneRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, camera_id: str, preset_name: str = "HOME") -> dict | None:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM restricted_zones WHERE camera_id = ? AND preset_name = ?",
                (camera_id, preset_name),
            ).fetchone()
        if row is None:
            return None
        output = dict(row)
        output["points"] = json.loads(output.pop("points_json"))
        return output

    def save(self, camera_id: str, preset_name: str, points) -> dict:
        clean = validate_normalized_polygon(points)
        timestamp = time.time()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO restricted_zones "
                "(camera_id, preset_name, points_json, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(camera_id, preset_name) DO UPDATE SET "
                "points_json = excluded.points_json, updated_at = excluded.updated_at",
                (camera_id, preset_name, json.dumps(clean), timestamp),
            )
        return self.get(camera_id, preset_name)


class DetectorSettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, camera_id: str, detector: str) -> dict:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM detector_settings WHERE camera_id = ? AND detector = ?",
                (camera_id, detector),
            ).fetchone()
        if row is None:
            return {
                "camera_id": camera_id,
                "detector": detector,
                "enabled": False,
                "overlay_enabled": True,
                "updated_at": None,
            }
        output = dict(row)
        output["enabled"] = bool(output["enabled"])
        output["overlay_enabled"] = bool(output["overlay_enabled"])
        return output

    def save(
        self,
        camera_id: str,
        detector: str,
        enabled: bool,
        overlay_enabled: bool,
    ) -> dict:
        timestamp = time.time()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO detector_settings "
                "(camera_id, detector, enabled, overlay_enabled, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(camera_id, detector) DO UPDATE SET "
                "enabled = excluded.enabled, overlay_enabled = excluded.overlay_enabled, "
                "updated_at = excluded.updated_at",
                (
                    camera_id,
                    detector,
                    int(bool(enabled)),
                    int(bool(overlay_enabled)),
                    timestamp,
                ),
            )
        return self.get(camera_id, detector)
