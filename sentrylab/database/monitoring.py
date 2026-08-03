"""Concise periodic SAFE monitoring records."""

from __future__ import annotations

from sentrylab.database.connection import Database


class MonitoringLogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add_safe(
        self,
        camera_id: str,
        message: str,
        people_count: int,
        timestamp: float,
    ) -> dict:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO monitoring_logs "
                "(camera_id, level, message, people_count, created_at) "
                "VALUES (?, 'SAFE', ?, ?, ?)",
                (
                    str(camera_id),
                    str(message),
                    max(0, int(people_count)),
                    float(timestamp),
                ),
            )
            row = connection.execute(
                "SELECT * FROM monitoring_logs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def list(
        self,
        camera_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        where = "WHERE camera_id = ?" if camera_id else ""
        parameters = [camera_id] if camera_id else []
        parameters.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self.database.session() as connection:
            rows = connection.execute(
                f"SELECT * FROM monitoring_logs {where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self, camera_id: str | None = None) -> int:
        where = "WHERE camera_id = ?" if camera_id else ""
        parameters = [camera_id] if camera_id else []
        with self.database.session() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) count FROM monitoring_logs {where}",
                parameters,
            ).fetchone()
        return int(row["count"] or 0)
