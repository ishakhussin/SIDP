"""Responsive CAM service for tracked-person metric proximity."""

from __future__ import annotations

import logging
import threading
import time

from sentrylab.database.zones import UNSAFE_PROXIMITY_DETECTOR
from sentrylab.domain.detection import SafetyLevel
from sentrylab.services.incident_processor import IncidentVoteProcessor


LOGGER = logging.getLogger(__name__)


class UnsafeProximityService:
    def __init__(
        self,
        camera_id,
        camera_worker,
        detector,
        settings_repository,
        incident_repository,
        evidence_recorder,
        person_interval_seconds: float = 0.18,
        depth_interval_seconds: float = 2.0,
        vote_interval_seconds: float = 1.0,
        result_ttl_seconds: float = 5.0,
    ) -> None:
        self.camera_id = camera_id
        self.camera_worker = camera_worker
        self.detector = detector
        self.settings_repository = settings_repository
        self.processor = IncidentVoteProcessor(
            incident_repository, vote_interval_seconds=vote_interval_seconds
        )
        self.evidence_recorder = evidence_recorder
        self.person_interval_seconds = float(person_interval_seconds)
        self.depth_interval_seconds = float(depth_interval_seconds)
        self.vote_interval_seconds = float(vote_interval_seconds)
        self.result_ttl_seconds = float(result_ttl_seconds)

        self._lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._depth_thread = None
        self._depth_processing = False
        self._last_sequence = -1
        self._last_people_at = None
        self._last_depth_started_at = None
        self._last_vote_at = None
        self._people = []
        self._detections = []
        self._confirmed_levels = {}
        self._depth_result = None
        self._last_error = None
        self._last_processed_at = None
        self._last_depth_ms = None

    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name=f"unsafe-proximity-{self.camera_id.replace(' ', '-').lower()}",
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        depth_thread = self._depth_thread
        if depth_thread is not None and depth_thread.is_alive():
            depth_thread.join(timeout)

    def _start_depth(self, frame, captured_at: float) -> None:
        with self._lock:
            if self._depth_processing:
                return
            self._depth_processing = True
            self._last_depth_started_at = captured_at

        def run():
            started = time.perf_counter()
            try:
                result = self.detector.estimate_depth(frame, captured_at)
                with self._lock:
                    self._depth_result = result
                    self._last_depth_ms = (time.perf_counter() - started) * 1000.0
                    self._last_error = None
            except Exception as error:
                with self._lock:
                    self._last_error = str(error)
                LOGGER.exception("Unsafe Proximity depth failed for %s", self.camera_id)
            finally:
                with self._lock:
                    self._depth_processing = False

        self._depth_thread = threading.Thread(
            target=run,
            daemon=True,
            name=f"depth-{self.camera_id.replace(' ', '-').lower()}",
        )
        self._depth_thread.start()

    def process_latest_once(self, timestamp: float | None = None) -> bool:
        settings = self.settings_repository.get(
            self.camera_id, UNSAFE_PROXIMITY_DETECTOR
        )
        if not settings["enabled"]:
            return False
        latest = self.camera_worker.get_latest_frame(copy=True)
        if latest is None or latest.sequence == self._last_sequence:
            return False
        self._last_sequence = latest.sequence
        now = float(timestamp if timestamp is not None else latest.captured_at)

        try:
            with self._process_lock:
                if (
                    self._last_people_at is None
                    or now - self._last_people_at >= self.person_interval_seconds
                ):
                    people = self.detector.detect_people(latest.frame)
                    self._last_people_at = now
                    with self._lock:
                        self._people = list(people)
                else:
                    with self._lock:
                        people = list(self._people)

                with self._lock:
                    depth_processing = self._depth_processing
                    last_depth_started = self._last_depth_started_at
                if (
                    len(people) >= 2
                    and not depth_processing
                    and (
                        last_depth_started is None
                        or now - last_depth_started >= self.depth_interval_seconds
                    )
                ):
                    self._start_depth(latest.frame.copy(), now)

                should_vote = (
                    self._last_vote_at is None
                    or now - self._last_vote_at >= self.vote_interval_seconds
                )
                if should_vote:
                    with self._lock:
                        depth_result = self._depth_result
                    sampled = self.detector.measure(
                        people,
                        depth_result,
                        latest.frame.shape[1],
                        self.camera_id,
                        now,
                        self.result_ttl_seconds,
                    )
                    levels = self.processor.process(sampled, now)
                    self._last_vote_at = now
                    for incident_id in self.processor.take_new_unsafe_incident_ids():
                        self.evidence_recorder.trigger(
                            incident_id, now, settings["overlay_enabled"]
                        )
                    valid = [
                        detection for detection in sampled
                        if detection.observation.level is not SafetyLevel.UNKNOWN
                    ]
                    with self._lock:
                        if valid:
                            self._detections = valid
                        elif self._detections:
                            newest = max(
                                detection.observation.timestamp
                                for detection in self._detections
                            )
                            if now - newest > self.result_ttl_seconds:
                                self._detections = []
                        self._confirmed_levels = dict(levels)

                with self._lock:
                    detections = list(self._detections)
                    levels = dict(self._confirmed_levels)
                    depth_processing = self._depth_processing
                evidence_frame = (
                    self.detector.draw_overlay(
                        latest.frame, people, detections, levels, depth_processing
                    )
                    if settings["overlay_enabled"]
                    else latest.frame
                )
                self.evidence_recorder.add_frame(evidence_frame, now)

            with self._lock:
                self._last_processed_at = now
            return True
        except Exception as error:
            with self._lock:
                self._last_error = str(error)
            LOGGER.exception("Unsafe Proximity failed for %s", self.camera_id)
            return False

    def close_active(self, reason: str) -> None:
        with self._process_lock:
            self.processor.close_all(time.time(), reason)
        with self._lock:
            self._detections = []
            self._confirmed_levels = {}

    def render(self, frame):
        settings = self.settings_repository.get(
            self.camera_id, UNSAFE_PROXIMITY_DETECTOR
        )
        if not settings["enabled"] or not settings["overlay_enabled"]:
            return frame
        with self._lock:
            people = list(self._people)
            detections = list(self._detections)
            levels = dict(self._confirmed_levels)
            processing = self._depth_processing
        return self.detector.draw_overlay(
            frame, people, detections, levels, processing
        )

    def status(self) -> dict:
        settings = self.settings_repository.get(
            self.camera_id, UNSAFE_PROXIMITY_DETECTOR
        )
        with self._lock:
            status = {
                "camera_id": self.camera_id,
                "detector": UNSAFE_PROXIMITY_DETECTOR,
                "running": self._thread is not None and self._thread.is_alive(),
                "enabled": settings["enabled"],
                "overlay_enabled": settings["overlay_enabled"],
                "person_model_loaded": self.detector.person_loaded,
                "depth_model_loaded": self.detector.depth_loaded,
                "people_count": len(self._people),
                "pair_count": len(self._detections),
                "levels": {
                    key: value.value for key, value in self._confirmed_levels.items()
                },
                "depth_processing": self._depth_processing,
                "last_depth_ms": self._last_depth_ms,
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
