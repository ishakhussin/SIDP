"""Background Restricted Zone service consuming a CameraWorker frame slot."""

from __future__ import annotations

import logging
import threading
import time

from sentrylab.database.zones import RESTRICTED_ZONE_DETECTOR
from sentrylab.services.restricted_zone_processor import RestrictedZoneProcessor


LOGGER = logging.getLogger(__name__)


class RestrictedZoneService:
    def __init__(
        self,
        camera_id,
        camera_worker,
        detector,
        zone_repository,
        settings_repository,
        incident_repository,
        evidence_recorder,
        preset_provider=lambda: "HOME",
        process_interval_seconds: float = 0.18,
    ) -> None:
        self.camera_id = camera_id
        self.camera_worker = camera_worker
        self.detector = detector
        self.zone_repository = zone_repository
        self.settings_repository = settings_repository
        self.processor = RestrictedZoneProcessor(incident_repository)
        self.evidence_recorder = evidence_recorder
        self.preset_provider = preset_provider
        self.process_interval_seconds = float(process_interval_seconds)

        self._lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_sequence = -1
        self._last_inference_at = None
        self._detections = []
        self._confirmed_levels = {}
        self._zone = None
        self._last_error = None
        self._last_processed_at = None

    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name=f"restricted-zone-{self.camera_id.replace(' ', '-').lower()}",
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def process_latest_once(self, timestamp: float | None = None) -> bool:
        settings = self.settings_repository.get(
            self.camera_id, RESTRICTED_ZONE_DETECTOR
        )
        if not settings["enabled"]:
            return False

        preset = self.preset_provider()
        zone = self.zone_repository.get(self.camera_id, preset)
        if zone is None or not zone["points"]:
            with self._lock:
                self._last_error = f"No restricted zone configured for preset {preset}"
            return False

        latest = self.camera_worker.get_latest_frame(copy=True)
        if latest is None or latest.sequence == self._last_sequence:
            return False
        self._last_sequence = latest.sequence

        now = float(timestamp if timestamp is not None else latest.captured_at)
        if (
            self._last_inference_at is not None
            and now - self._last_inference_at < self.process_interval_seconds
        ):
            return False
        self._last_inference_at = now
        try:
            with self._process_lock:
                detections = self.detector.detect(
                    latest.frame,
                    zone["points"],
                    self.camera_id,
                    now,
                )
                levels = self.processor.process(detections, now)
                unsafe_incident_ids = self.processor.take_new_unsafe_incident_ids()
                for incident_id in unsafe_incident_ids:
                    self.evidence_recorder.trigger(
                        incident_id,
                        now,
                        settings["overlay_enabled"],
                    )
                try:
                    evidence_frame = (
                        self.detector.draw_overlay(
                            latest.frame, zone["points"], detections, levels
                        )
                        if settings["overlay_enabled"]
                        else latest.frame
                    )
                    self.evidence_recorder.add_frame(evidence_frame, now)
                except Exception as evidence_error:
                    self.evidence_recorder.record_error(evidence_error)
                    LOGGER.exception(
                        "Evidence buffering failed for %s", self.camera_id
                    )
            with self._lock:
                self._detections = list(detections)
                self._confirmed_levels = dict(levels)
                self._zone = zone
                self._last_error = None
                self._last_processed_at = now
            return True
        except Exception as error:
            with self._lock:
                self._last_error = str(error)
            LOGGER.exception("Restricted Zone failed for %s", self.camera_id)
            return False

    def close_active(self, reason: str) -> None:
        with self._process_lock:
            self.processor.close_all(time.time(), reason)
        with self._lock:
            self._detections = []
            self._confirmed_levels = {}

    def render(self, frame):
        settings = self.settings_repository.get(
            self.camera_id, RESTRICTED_ZONE_DETECTOR
        )
        if not settings["enabled"] or not settings["overlay_enabled"]:
            return frame
        with self._lock:
            zone = self._zone
            detections = list(self._detections)
            levels = dict(self._confirmed_levels)
        if zone is None:
            return frame
        return self.detector.draw_overlay(frame, zone["points"], detections, levels)

    def status(self) -> dict:
        settings = self.settings_repository.get(
            self.camera_id, RESTRICTED_ZONE_DETECTOR
        )
        with self._lock:
            status = {
                "camera_id": self.camera_id,
                "detector": RESTRICTED_ZONE_DETECTOR,
                "running": self._thread is not None and self._thread.is_alive(),
                "enabled": settings["enabled"],
                "overlay_enabled": settings["overlay_enabled"],
                "model_loaded": self.detector.loaded,
                "people_count": len(self._detections),
                "levels": {
                    key: value.value for key, value in self._confirmed_levels.items()
                },
                "last_processed_at": self._last_processed_at,
                "last_error": self._last_error,
            }
        status.update(self.evidence_recorder.status())
        return status

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                processed = self.process_latest_once()
                self.evidence_recorder.poll(time.time())
                self._stop_event.wait(0.01 if processed else 0.05)
        finally:
            self.evidence_recorder.stop()
