"""Lifecycle registry for detector services; imports models only when enabled."""

from __future__ import annotations

import logging
import threading
import time

from sentrylab.database.zones import (
    PPE_COMPLIANCE_DETECTOR,
    RESTRICTED_ZONE_DETECTOR,
    UNSAFE_PROXIMITY_DETECTOR,
)


SUPPORTED_DETECTORS = (
    RESTRICTED_ZONE_DETECTOR,
    UNSAFE_PROXIMITY_DETECTOR,
    PPE_COMPLIANCE_DETECTOR,
)


LOGGER = logging.getLogger(__name__)


class DetectionManager:
    def __init__(
        self,
        camera_manager,
        zone_repository,
        settings_repository,
        incident_repository,
        model_dir,
        clips_dir,
    ) -> None:
        self.camera_manager = camera_manager
        self.zone_repository = zone_repository
        self.settings_repository = settings_repository
        self.incident_repository = incident_repository
        self.model_dir = model_dir
        self.clips_dir = clips_dir
        self._services = {}
        self._render_errors = {}
        self._lock = threading.RLock()
        self._inference_lock = threading.Lock()
        self._runtime_started = False

    def start_enabled(self) -> None:
        self._runtime_started = True
        for camera in self.camera_manager.statuses():
            self.apply_settings(camera["camera_id"])

    def apply_settings(self, camera_id: str, detector: str | None = None) -> None:
        detectors = (detector,) if detector else SUPPORTED_DETECTORS
        for detector_name in detectors:
            settings = self.settings_repository.get(camera_id, detector_name)
            key = (camera_id, detector_name)
            if not settings["enabled"]:
                with self._lock:
                    service = self._services.get(key)
                if service is not None:
                    service.close_active("detector disabled")
                continue
            if not self._runtime_started:
                continue
            if detector_name == RESTRICTED_ZONE_DETECTOR:
                self._ensure_restricted_zone(camera_id)
            elif detector_name == UNSAFE_PROXIMITY_DETECTOR:
                self._ensure_unsafe_proximity(camera_id)
            elif detector_name == PPE_COMPLIANCE_DETECTOR:
                self._ensure_ppe(camera_id)

    def _ensure_restricted_zone(self, camera_id: str):
        with self._lock:
            key = (camera_id, RESTRICTED_ZONE_DETECTOR)
            service = self._services.get(key)
            if service is not None:
                service.start()
                return service

            worker = self.camera_manager.existing_worker(camera_id)
            if worker is None:
                return None

            self.incident_repository.close_stale_active(
                camera_id,
                RESTRICTED_ZONE_DETECTOR,
                time.time(),
            )

            from sentrylab.detection.restricted_zone import RestrictedZoneDetector
            from sentrylab.services.evidence_recorder import EvidenceClipRecorder
            from sentrylab.services.restricted_zone_service import RestrictedZoneService

            detector = RestrictedZoneDetector(
                self.model_dir / "restricted_zone" / "yolo11n-pose.pt",
                inference_lock=self._inference_lock,
            )
            recorder = EvidenceClipRecorder(
                camera_id=camera_id,
                detector=RESTRICTED_ZONE_DETECTOR,
                clips_dir=self.clips_dir,
                incident_repository=self.incident_repository,
            )
            service = RestrictedZoneService(
                camera_id=camera_id,
                camera_worker=worker,
                detector=detector,
                zone_repository=self.zone_repository,
                settings_repository=self.settings_repository,
                incident_repository=self.incident_repository,
                evidence_recorder=recorder,
            )
            self._services[key] = service
            service.start()
            return service

    def _ensure_unsafe_proximity(self, camera_id: str):
        with self._lock:
            key = (camera_id, UNSAFE_PROXIMITY_DETECTOR)
            service = self._services.get(key)
            if service is not None:
                service.start()
                return service

            worker = self.camera_manager.existing_worker(camera_id)
            if worker is None:
                return None
            self.incident_repository.close_stale_active(
                camera_id,
                UNSAFE_PROXIMITY_DETECTOR,
                time.time(),
            )

            from sentrylab.detection.unsafe_proximity import UnsafeProximityDetector
            from sentrylab.services.evidence_recorder import EvidenceClipRecorder
            from sentrylab.services.unsafe_proximity_service import UnsafeProximityService

            detector = UnsafeProximityDetector(
                self.model_dir / "unsafe_proximity",
                inference_lock=self._inference_lock,
            )
            recorder = EvidenceClipRecorder(
                camera_id=camera_id,
                detector=UNSAFE_PROXIMITY_DETECTOR,
                clips_dir=self.clips_dir,
                incident_repository=self.incident_repository,
            )
            service = UnsafeProximityService(
                camera_id=camera_id,
                camera_worker=worker,
                detector=detector,
                settings_repository=self.settings_repository,
                incident_repository=self.incident_repository,
                evidence_recorder=recorder,
            )
            self._services[key] = service
            service.start()
            return service

    def _ensure_ppe(self, camera_id: str):
        with self._lock:
            key = (camera_id, PPE_COMPLIANCE_DETECTOR)
            service = self._services.get(key)
            if service is not None:
                service.start()
                return service
            worker = self.camera_manager.existing_worker(camera_id)
            if worker is None:
                return None
            self.incident_repository.close_stale_active(
                camera_id, PPE_COMPLIANCE_DETECTOR, time.time()
            )
            from sentrylab.detection.ppe import PPEComplianceDetector
            from sentrylab.services.evidence_recorder import EvidenceClipRecorder
            from sentrylab.services.ppe_service import PPEComplianceService

            detector = PPEComplianceDetector(
                self.model_dir / "ppe", inference_lock=self._inference_lock
            )
            recorder = EvidenceClipRecorder(
                camera_id=camera_id, detector=PPE_COMPLIANCE_DETECTOR,
                clips_dir=self.clips_dir, incident_repository=self.incident_repository,
            )
            service = PPEComplianceService(
                camera_id, worker, detector, self.settings_repository,
                self.incident_repository, recorder,
            )
            self._services[key] = service
            service.start()
            return service

    def render(self, camera_id: str, frame):
        with self._lock:
            services = [
                (detector, self._services.get((camera_id, detector)))
                for detector in SUPPORTED_DETECTORS
            ]
        output = frame
        for detector, service in services:
            if service is not None:
                try:
                    output = service.render(output)
                    with self._lock:
                        self._render_errors.pop((camera_id, detector), None)
                except Exception as error:
                    with self._lock:
                        self._render_errors[(camera_id, detector)] = str(error)
                    LOGGER.exception(
                        "%s overlay failed for %s; returning the remaining overlays",
                        detector,
                        camera_id,
                    )
        return output

    def status(self, camera_id: str, detector: str = RESTRICTED_ZONE_DETECTOR) -> dict:
        with self._lock:
            service = self._services.get((camera_id, detector))
            render_error = self._render_errors.get((camera_id, detector))
        if service is not None:
            status = service.status()
            if render_error:
                status["last_error"] = f"Overlay error: {render_error}"
            return status
        settings = self.settings_repository.get(camera_id, detector)
        base = {
            "camera_id": camera_id,
            "detector": detector,
            "running": False,
            "enabled": settings["enabled"],
            "overlay_enabled": settings["overlay_enabled"],
            "people_count": 0,
            "levels": {},
            "last_processed_at": None,
            "last_error": None,
            "active_recordings": 0,
            "clips_written": 0,
            "last_clip_error": None,
            "buffered_frames": 0,
        }
        if detector == UNSAFE_PROXIMITY_DETECTOR:
            base.update({
                "person_model_loaded": False,
                "depth_model_loaded": False,
                "pair_count": 0,
                "depth_processing": False,
                "last_depth_ms": None,
            })
        else:
            base["model_loaded"] = False
        if detector == PPE_COMPLIANCE_DETECTOR:
            base.update({
                "device": "not-loaded", "required_items": ["coat", "mask", "gloves"],
                "violation_count": 0,
            })
        if render_error:
            base["last_error"] = f"Overlay error: {render_error}"
        return base

    def stop_all(self) -> None:
        self._runtime_started = False
        with self._lock:
            services = list(self._services.values())
        for service in services:
            service.close_active("runtime stopped")
            service.stop()
        with self._lock:
            self._services.clear()
            self._render_errors.clear()
