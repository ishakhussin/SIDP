"""Tracked-person and metric-depth adapter for unsafe proximity."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sentrylab.domain.detection import DetectionObservation, SafetyLevel, SubjectKind


DETECTOR_NAME = "unsafe_proximity"


@dataclass(frozen=True)
class PersonTrack:
    track_id: int
    box: tuple[int, int, int, int]
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) // 2, (y1 + y2) // 2)


@dataclass(frozen=True)
class DepthResult:
    depth: np.ndarray
    focal_length: float
    captured_at: float


@dataclass(frozen=True)
class ProximityDetection:
    observation: DetectionObservation
    first_box: tuple[int, int, int, int]
    second_box: tuple[int, int, int, int]
    distance_metres: float | None

    @property
    def centers(self) -> tuple[tuple[int, int], tuple[int, int]]:
        def center(box):
            return ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        return center(self.first_box), center(self.second_box)


class UnsafeProximityDetector:
    """Loads both models lazily and exposes small independently testable steps."""

    def __init__(
        self,
        model_dir: Path,
        person_model_factory=None,
        depth_components_factory=None,
        minimum_distance_metres: float = 1.5,
        person_confidence: float = 0.40,
        minimum_person_height_ratio: float = 0.20,
        inference_lock=None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.person_model_factory = person_model_factory
        self.depth_components_factory = depth_components_factory
        self.minimum_distance_metres = float(minimum_distance_metres)
        self.person_confidence = float(person_confidence)
        self.minimum_person_height_ratio = float(minimum_person_height_ratio)
        self.inference_lock = inference_lock
        self._person_model = None
        self._processor = None
        self._depth_model = None
        self._torch = None
        self._device = None
        self._dtype = None

    @property
    def person_loaded(self) -> bool:
        return self._person_model is not None

    @property
    def depth_loaded(self) -> bool:
        return self._depth_model is not None

    @property
    def loaded(self) -> bool:
        return self.person_loaded and self.depth_loaded

    def _load_person_model(self) -> None:
        if self._person_model is not None:
            return
        path = self.model_dir / "yolo11n.pt"
        if self.person_model_factory is not None:
            self._person_model = self.person_model_factory(path)
            return
        if not path.is_file():
            raise FileNotFoundError(f"Unsafe Proximity person model not found: {path}")
        from ultralytics import YOLO

        self._person_model = YOLO(str(path))

    def _load_depth_model(self) -> None:
        if self._depth_model is not None:
            return
        if self.depth_components_factory is not None:
            self._processor, self._depth_model = self.depth_components_factory(
                self.model_dir
            )
            return
        required = ("config.json", "preprocessor_config.json", "model.safetensors")
        missing = [name for name in required if not (self.model_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"Unsafe Proximity DepthPro files missing: {', '.join(missing)}"
            )
        import torch
        from transformers import DepthProForDepthEstimation, DepthProImageProcessor

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._dtype = torch.float16 if self._device == "cuda" else torch.float32
        self._processor = DepthProImageProcessor.from_pretrained(
            self.model_dir, local_files_only=True
        )
        self._depth_model = DepthProForDepthEstimation.from_pretrained(
            self.model_dir,
            dtype=self._dtype,
            local_files_only=True,
        ).to(self._device).eval()

    def detect_people(self, frame) -> list[PersonTrack]:
        with self.inference_lock or nullcontext():
            self._load_person_model()
            result = self._person_model.track(
                frame,
                classes=[0],
                conf=self.person_confidence,
                imgsz=640,
                persist=True,
                verbose=False,
            )[0]
        boxes_object = getattr(result, "boxes", None)
        if boxes_object is None or getattr(boxes_object, "id", None) is None:
            return []
        boxes = np.asarray(boxes_object.xyxy.cpu().numpy()).astype(int)
        ids = np.asarray(boxes_object.id.cpu().numpy()).astype(int)
        confidences = np.asarray(boxes_object.conf.cpu().numpy(), dtype=float)
        minimum_height = frame.shape[0] * self.minimum_person_height_ratio
        people = []
        for box, track_id, confidence in zip(boxes, ids, confidences):
            clean_box = tuple(int(value) for value in box)
            if clean_box[3] - clean_box[1] < minimum_height:
                continue
            people.append(PersonTrack(int(track_id), clean_box, float(confidence)))
        return sorted(people, key=lambda person: person.track_id)

    def estimate_depth(self, frame, captured_at: float) -> DepthResult:
        # DepthPro receives one uninterrupted GPU slot. Without coordination,
        # continuous pose inference can stretch a 1.5 s depth pass beyond 30 s.
        with self.inference_lock or nullcontext():
            self._load_depth_model()
            if self.depth_components_factory is not None:
                depth, focal = self._depth_model(frame)
                return DepthResult(np.asarray(depth), float(focal), float(captured_at))

            import cv2

            torch = self._torch
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            inputs = self._processor(images=rgb, return_tensors="pt")
            inputs = {
                key: (
                    value.to(device=self._device, dtype=self._dtype)
                    if torch.is_floating_point(value)
                    else value.to(self._device)
                )
                for key, value in inputs.items()
            }
            with torch.inference_mode(), torch.autocast(
                device_type="cuda",
                dtype=torch.float16,
                enabled=self._device == "cuda",
            ):
                outputs = self._depth_model(**inputs)
            post = self._processor.post_process_depth_estimation(
                outputs, target_sizes=[frame.shape[:2]]
            )[0]
            depth = post["predicted_depth"].detach().float().cpu().numpy()
            focal = float(post["focal_length"].detach().float().cpu().reshape(-1)[0])
            return DepthResult(depth, focal, float(captured_at))

    @staticmethod
    def _person_point(person: PersonTrack, depth: DepthResult, center_x: float):
        x1, y1, x2, y2 = person.box
        width, height = x2 - x1, y2 - y1
        patch = depth.depth[
            max(0, int(y1 + 0.25 * height)):min(depth.depth.shape[0], int(y1 + 0.60 * height)),
            max(0, int(x1 + 0.30 * width)):min(depth.depth.shape[1], int(x1 + 0.70 * width)),
        ]
        valid = patch[np.isfinite(patch) & (patch > 0)]
        if not valid.size or depth.focal_length <= 0:
            return None
        z = float(np.median(valid))
        pixel_x = (x1 + x2) / 2.0
        return np.asarray([
            (pixel_x - center_x) * z / depth.focal_length,
            z,
        ])

    def measure(
        self,
        people: list[PersonTrack],
        depth: DepthResult | None,
        frame_width: int,
        camera_id: str,
        timestamp: float,
        maximum_depth_age: float = 5.0,
    ) -> list[ProximityDetection]:
        detections = []
        for first_index, first in enumerate(people):
            for second in people[first_index + 1:]:
                ordered = sorted((first, second), key=lambda person: person.track_id)
                first_person, second_person = ordered
                subject_id = f"person-{first_person.track_id}|person-{second_person.track_id}"
                distance = None
                level = SafetyLevel.UNKNOWN
                depth_age = None
                if depth is not None:
                    depth_age = max(0.0, float(timestamp) - depth.captured_at)
                    if depth_age <= maximum_depth_age:
                        first_point = self._person_point(
                            first_person, depth, frame_width / 2.0
                        )
                        second_point = self._person_point(
                            second_person, depth, frame_width / 2.0
                        )
                        if first_point is not None and second_point is not None:
                            distance = float(np.hypot(*(first_point - second_point)))
                            level = (
                                SafetyLevel.WARNING
                                if distance < self.minimum_distance_metres
                                else SafetyLevel.SAFE
                            )
                observation = DetectionObservation(
                    camera_id=camera_id,
                    detector=DETECTOR_NAME,
                    subject_id=subject_id,
                    subject_kind=SubjectKind.PAIR,
                    level=level,
                    timestamp=float(timestamp),
                    confidence=min(first_person.confidence, second_person.confidence),
                    metadata={
                        "distance_metres": distance,
                        "minimum_distance_metres": self.minimum_distance_metres,
                        "depth_age_seconds": depth_age,
                        "person_track_ids": [first_person.track_id, second_person.track_id],
                        "boxes": [list(first_person.box), list(second_person.box)],
                    },
                )
                detections.append(ProximityDetection(
                    observation,
                    first_person.box,
                    second_person.box,
                    distance,
                ))
        return detections

    def draw_overlay(self, frame, people, detections, levels, depth_processing=False):
        import cv2

        output = frame.copy()
        for person in people:
            x1, y1, x2, y2 = person.box
            cv2.rectangle(output, (x1, y1), (x2, y2), (0, 220, 255), 2)
            cv2.putText(
                output,
                f"Person {person.track_id} {person.confidence:.2f}",
                (x1, max(24, y1 - 7)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 220, 255),
                2,
            )
        colors = {
            SafetyLevel.SAFE: (0, 220, 0),
            SafetyLevel.WARNING: (0, 165, 255),
            SafetyLevel.UNSAFE: (0, 0, 255),
            SafetyLevel.UNKNOWN: (160, 160, 160),
        }
        for detection in detections:
            if detection.distance_metres is None:
                continue
            level = levels.get(
                detection.observation.subject_id, detection.observation.level
            )
            color = colors[level]
            first, second = detection.centers
            midpoint = ((first[0] + second[0]) // 2, (first[1] + second[1]) // 2)
            cv2.line(output, first, second, color, 4)
            cv2.putText(
                output,
                f"{detection.distance_metres:.2f} m | {level.value}",
                midpoint,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                color,
                3,
            )
        if depth_processing:
            cv2.putText(
                output,
                "Depth updating...",
                (18, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
        return output
