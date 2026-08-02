"""Background PPE compliance service consuming the shared camera frame slot."""

from __future__ import annotations

import logging
import threading
import time

from sentrylab.database.zones import PPE_COMPLIANCE_DETECTOR
from sentrylab.services.incident_processor import IncidentVoteProcessor


LOGGER = logging.getLogger(__name__)


class PPEComplianceService:
    def __init__(self, camera_id, camera_worker, detector, settings_repository,
                 incident_repository, evidence_recorder, process_interval_seconds=0.30):
        self.camera_id = camera_id
        self.camera_worker = camera_worker
        self.detector = detector
        self.settings_repository = settings_repository
        self.processor = IncidentVoteProcessor(incident_repository)
        self.evidence_recorder = evidence_recorder
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
        self._last_error = None
        self._last_processed_at = None

    def start(self):
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True,
                name=f"ppe-{self.camera_id.replace(' ', '-').lower()}")
            self._thread.start()

    def stop(self, timeout=3.0):
        self._stop_event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def process_latest_once(self, timestamp=None):
        settings = self.settings_repository.get(self.camera_id, PPE_COMPLIANCE_DETECTOR)
        if not settings["enabled"]:
            return False
        latest = self.camera_worker.get_latest_frame(copy=True)
        if latest is None or latest.sequence == self._last_sequence:
            return False
        self._last_sequence = latest.sequence
        now = float(timestamp if timestamp is not None else latest.captured_at)
        if self._last_inference_at is not None and now - self._last_inference_at < self.process_interval_seconds:
            return False
        self._last_inference_at = now
        try:
            with self._process_lock:
                detections = self.detector.detect(latest.frame, self.camera_id, now)
                levels = self.processor.process(detections, now)
                for incident_id in self.processor.take_new_unsafe_incident_ids():
                    self.evidence_recorder.trigger(incident_id, now, settings["overlay_enabled"])
                evidence_frame = self.detector.draw_overlay(latest.frame, detections, levels) \
                    if settings["overlay_enabled"] else latest.frame
                self.evidence_recorder.add_frame(evidence_frame, now)
            with self._lock:
                self._detections = list(detections)
                self._confirmed_levels = dict(levels)
                self._last_error = None
                self._last_processed_at = now
            return True
        except Exception as error:
            with self._lock:
                self._last_error = str(error)
            LOGGER.exception("PPE Compliance failed for %s", self.camera_id)
            return False

    def close_active(self, reason):
        with self._process_lock:
            self.processor.close_all(time.time(), reason)
        with self._lock:
            self._detections = []
            self._confirmed_levels = {}

    def render(self, frame):
        settings = self.settings_repository.get(self.camera_id, PPE_COMPLIANCE_DETECTOR)
        if not settings["enabled"] or not settings["overlay_enabled"]:
            return frame
        with self._lock:
            detections = list(self._detections)
            levels = dict(self._confirmed_levels)
        return self.detector.draw_overlay(frame, detections, levels)

    def status(self):
        settings = self.settings_repository.get(self.camera_id, PPE_COMPLIANCE_DETECTOR)
        with self._lock:
            status = {
                "camera_id": self.camera_id, "detector": PPE_COMPLIANCE_DETECTOR,
                "running": self._thread is not None and self._thread.is_alive(),
                "enabled": settings["enabled"], "overlay_enabled": settings["overlay_enabled"],
                "model_loaded": self.detector.loaded, "device": self.detector.device,
                "required_items": ["coat", "mask", "gloves"],
                "people_count": len(self._detections),
                "violation_count": sum(bool(item.missing_items) for item in self._detections),
                "levels": {key: value.value for key, value in self._confirmed_levels.items()},
                "last_processed_at": self._last_processed_at, "last_error": self._last_error,
            }
        status.update(self.evidence_recorder.status())
        return status

    def _run(self):
        try:
            while not self._stop_event.is_set():
                processed = self.process_latest_once()
                self.evidence_recorder.poll(time.time())
                self._stop_event.wait(0.01 if processed else 0.05)
        finally:
            self.evidence_recorder.stop()
