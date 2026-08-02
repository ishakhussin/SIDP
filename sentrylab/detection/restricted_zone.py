"""YOLO11 pose adapter preserving the original ankle geofence logic."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sentrylab.domain.detection import (
    DetectionObservation,
    SafetyLevel,
    SubjectKind,
)


LEFT_ANKLE = 15
RIGHT_ANKLE = 16
DETECTOR_NAME = "restricted_zone"


def point_in_polygon(point, polygon) -> bool:
    """Boundary-inclusive ray casting for normalized or pixel coordinates."""
    x, y = float(point[0]), float(point[1])
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]

        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) < 1e-7 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            return True

        intersects = (y1 > y) != (y2 > y)
        if intersects:
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
    return inside


def pixel_polygon(normalized_points, width: int, height: int):
    return [
        (float(x) * width, float(y) * height)
        for x, y in normalized_points
    ]


@dataclass(frozen=True)
class RestrictedZoneDetection:
    observation: DetectionObservation
    check_points: tuple[tuple[float, float], ...]
    points_inside: tuple[bool, ...]
    used_fallback: bool


class RestrictedZoneDetector:
    def __init__(
        self,
        model_path: Path,
        model_factory=None,
        person_confidence: float = 0.30,
        keypoint_confidence: float = 0.50,
        inference_lock=None,
    ) -> None:
        self.model_path = Path(model_path)
        self.model_factory = model_factory
        self.person_confidence = float(person_confidence)
        self.keypoint_confidence = float(keypoint_confidence)
        self.inference_lock = inference_lock
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        if not self.model_path.is_file() and self.model_factory is None:
            raise FileNotFoundError(f"Restricted Zone model not found: {self.model_path}")
        if self.model_factory is None:
            from ultralytics import YOLO

            self._model = YOLO(str(self.model_path))
        else:
            self._model = self.model_factory(self.model_path)

    def detect(
        self,
        frame,
        normalized_zone,
        camera_id: str,
        timestamp: float,
    ) -> list[RestrictedZoneDetection]:
        self.load()
        height, width = frame.shape[:2]
        polygon = pixel_polygon(normalized_zone, width, height)
        with self.inference_lock or nullcontext():
            result = self._model.track(
                frame,
                classes=[0],
                conf=self.person_confidence,
                persist=True,
                verbose=False,
            )[0]

        boxes_object = getattr(result, "boxes", None)
        keypoints_object = getattr(result, "keypoints", None)
        if (
            boxes_object is None
            or getattr(boxes_object, "id", None) is None
            or keypoints_object is None
        ):
            return []

        boxes = np.asarray(boxes_object.xyxy.cpu().numpy()).astype(int)
        track_ids = np.asarray(boxes_object.id.cpu().numpy()).astype(int)
        coordinates = np.asarray(keypoints_object.xy.cpu().numpy())
        confidence_object = getattr(keypoints_object, "conf", None)
        confidences = (
            np.asarray(confidence_object.cpu().numpy())
            if confidence_object is not None
            else np.zeros((len(track_ids), 17), dtype=float)
        )

        detections = []
        for box, track_id, keypoints, confidence in zip(
            boxes, track_ids, coordinates, confidences
        ):
            x1, y1, x2, y2 = [int(value) for value in box]
            trusted = []
            for index in (LEFT_ANKLE, RIGHT_ANKLE):
                if confidence[index] >= self.keypoint_confidence:
                    trusted.append(tuple(float(value) for value in keypoints[index]))

            used_fallback = not trusted
            check_points = trusted or [((x1 + x2) / 2.0, float(y2))]
            points_inside = [point_in_polygon(point, polygon) for point in check_points]
            raw_level = SafetyLevel.WARNING if any(points_inside) else SafetyLevel.SAFE
            observation = DetectionObservation(
                camera_id=camera_id,
                detector=DETECTOR_NAME,
                subject_id=str(track_id),
                subject_kind=SubjectKind.PERSON,
                level=raw_level,
                timestamp=float(timestamp),
                box=(x1, y1, x2, y2),
                metadata={
                    "used_fallback": used_fallback,
                    "check_points": [list(point) for point in check_points],
                    "points_inside": points_inside,
                },
            )
            detections.append(RestrictedZoneDetection(
                observation=observation,
                check_points=tuple(check_points),
                points_inside=tuple(points_inside),
                used_fallback=used_fallback,
            ))
        return detections

    @staticmethod
    def draw_overlay(frame, normalized_zone, detections, confirmed_levels=None):
        import cv2

        output = frame.copy()
        height, width = output.shape[:2]
        polygon = np.asarray(pixel_polygon(normalized_zone, width, height), dtype=np.int32)
        layer = output.copy()
        cv2.fillPoly(layer, [polygon], (0, 0, 255))
        cv2.addWeighted(layer, 0.18, output, 0.82, 0, output)
        cv2.polylines(output, [polygon], True, (0, 0, 255), 2)

        levels = confirmed_levels or {}
        colors = {
            SafetyLevel.SAFE: (0, 255, 0),
            SafetyLevel.WARNING: (0, 255, 255),
            SafetyLevel.UNSAFE: (0, 0, 255),
            SafetyLevel.UNKNOWN: (255, 255, 255),
        }
        for detection in detections:
            observation = detection.observation
            level = levels.get(observation.subject_id, observation.level)
            color = colors[level]
            x1, y1, x2, y2 = observation.box
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
            for point, inside in zip(detection.check_points, detection.points_inside):
                dot = (255, 255, 255) if detection.used_fallback else (
                    color if inside else (0, 255, 0)
                )
                cv2.circle(output, (int(point[0]), int(point[1])), 6, dot, -1)
            fallback = " FALLBACK" if detection.used_fallback else ""
            cv2.putText(
                output,
                f"Person {observation.subject_id}: {level.value}{fallback}",
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )
        return output
