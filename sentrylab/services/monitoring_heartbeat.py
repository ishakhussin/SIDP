"""Five-minute SAFE heartbeat for healthy online camera monitoring."""

from __future__ import annotations

import logging
import threading
import time

from sentrylab.services.detection_manager import SUPPORTED_DETECTORS


LOGGER = logging.getLogger(__name__)


class MonitoringHeartbeatService:
    def __init__(
        self,
        camera_manager,
        detection_manager,
        repository,
        interval_seconds: float = 300.0,
    ) -> None:
        self.camera_manager = camera_manager
        self.detection_manager = detection_manager
        self.repository = repository
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._last_due_check: dict[str, float] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._last_due_check.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="monitoring-safe-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def collect_once(self, timestamp: float | None = None) -> list[dict]:
        now = float(time.time() if timestamp is None else timestamp)
        created = []
        for camera in self.camera_manager.statuses():
            camera_id = camera["camera_id"]
            if camera.get("state") != "ONLINE":
                self._last_due_check.pop(camera_id, None)
                continue

            baseline = self._last_due_check.get(camera_id)
            if baseline is None:
                self._last_due_check[camera_id] = now
                continue
            if now - baseline < self.interval_seconds:
                continue
            self._last_due_check[camera_id] = now

            healthy_statuses = []
            for detector in SUPPORTED_DETECTORS:
                status = self.detection_manager.status(camera_id, detector)
                if (
                    status.get("enabled")
                    and status.get("running")
                    and status.get("last_processed_at") is not None
                    and not status.get("last_error")
                ):
                    healthy_statuses.append(status)
            if not healthy_statuses:
                continue

            levels = {
                level
                for status in healthy_statuses
                for level in status.get("levels", {}).values()
            }
            if levels.intersection({"WARNING", "UNSAFE"}):
                continue

            people_count = max(
                (int(status.get("people_count") or 0) for status in healthy_statuses),
                default=0,
            )
            if people_count == 0:
                message = "No activity detected"
            elif people_count == 1:
                message = "Person monitored, no violation"
            else:
                message = f"{people_count} people monitored, no violation"
            created.append(self.repository.add_safe(
                camera_id, message, people_count, now
            ))
        return created

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.collect_once()
            except Exception:
                LOGGER.exception("SAFE heartbeat collection failed")
            self._stop_event.wait(1.0)
