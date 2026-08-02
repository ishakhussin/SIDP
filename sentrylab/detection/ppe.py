"""Person-level coat, mask, and gloves compliance adapter."""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sentrylab.domain.detection import DetectionObservation, SafetyLevel, SubjectKind


DETECTOR_NAME = "ppe_compliance"
PPE_ITEMS = ("coat", "mask", "gloves")


@dataclass(frozen=True)
class PPEDetection:
    observation: DetectionObservation
    flags: dict[str, bool]
    probabilities: dict[str, float]
    missing_items: tuple[str, ...]


class PPEComplianceDetector:
    """YOLO person tracking plus the existing EfficientNet multi-label model."""

    def __init__(
        self,
        model_dir: Path,
        person_model=None,
        classifier=None,
        person_confidence: float = 0.40,
        item_thresholds: dict[str, float] | None = None,
        input_size: int = 320,
        smooth_frames: int = 3,
        inference_lock=None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.person_confidence = float(person_confidence)
        self.item_thresholds = {item: 0.50 for item in PPE_ITEMS}
        if item_thresholds:
            self.item_thresholds.update(item_thresholds)
        self.input_size = int(input_size)
        self.smooth_frames = max(1, int(smooth_frames))
        self.inference_lock = inference_lock
        self._person_model = person_model
        self._classifier = classifier
        self._device = None
        self._transform = None
        self._history = defaultdict(lambda: deque(maxlen=self.smooth_frames))

    @property
    def loaded(self) -> bool:
        return self._person_model is not None and self._classifier is not None

    @property
    def device(self) -> str:
        return self._device or "not-loaded"

    def load(self) -> None:
        if self.loaded:
            return
        person_path = self.model_dir / "yolov8n.pt"
        classifier_path = self.model_dir / "ppe_multilabel_best.pt"
        missing = [path for path in (person_path, classifier_path) if not path.is_file()]
        if missing:
            raise FileNotFoundError("PPE model not found: " + ", ".join(map(str, missing)))

        import torch
        from torchvision import models, transforms
        from ultralytics import YOLO

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._person_model = YOLO(str(person_path))
        classifier = models.efficientnet_b0(weights=None)
        in_features = classifier.classifier[1].in_features
        classifier.classifier[1] = torch.nn.Linear(in_features, len(PPE_ITEMS))
        checkpoint = torch.load(classifier_path, map_location=self._device)
        if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
            checkpoint = checkpoint["model_state_dict"]
        classifier.load_state_dict(checkpoint)
        self._classifier = classifier.to(self._device).eval()
        self._transform = transforms.Compose([
            transforms.Resize((self.input_size, self.input_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

    @staticmethod
    def _padded_box(box, width: int, height: int, pad: float = 0.08):
        x1, y1, x2, y2 = [float(value) for value in box]
        dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
        return (
            max(0, int(round(x1 - dx))),
            max(0, int(round(y1 - dy))),
            min(width - 1, int(round(x2 + dx))),
            min(height - 1, int(round(y2 + dy))),
        )

    def _classify(self, frame, boxes) -> np.ndarray:
        if not boxes:
            return np.zeros((0, len(PPE_ITEMS)), dtype=np.float32)
        if self._transform is None and callable(self._classifier):
            return np.asarray(self._classifier(frame, boxes), dtype=np.float32)

        import cv2
        import torch
        from PIL import Image

        tensors = []
        for x1, y1, x2, y2 in boxes:
            crop = frame[y1:y2, x1:x2]
            image = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            tensors.append(self._transform(image))
        batch = torch.stack(tensors).to(self._device)
        with torch.inference_mode():
            return torch.sigmoid(self._classifier(batch)).float().cpu().numpy()

    def detect(self, frame, camera_id: str, timestamp: float) -> list[PPEDetection]:
        self.load()
        height, width = frame.shape[:2]
        with self.inference_lock or nullcontext():
            result = self._person_model.track(
                frame,
                classes=[0],
                conf=self.person_confidence,
                persist=True,
                verbose=False,
            )[0]
            boxes_object = getattr(result, "boxes", None)
            if boxes_object is None or getattr(boxes_object, "id", None) is None:
                return []
            boxes = [self._padded_box(box, width, height) for box in np.asarray(
                boxes_object.xyxy.cpu().numpy()
            )]
            track_ids = np.asarray(boxes_object.id.cpu().numpy()).astype(int)
            raw_probabilities = self._classify(frame, boxes)

        detections = []
        live_ids = set()
        for track_id, box, raw in zip(track_ids, boxes, raw_probabilities):
            subject_id = str(int(track_id))
            live_ids.add(subject_id)
            self._history[subject_id].append(np.asarray(raw, dtype=np.float32))
            probabilities = np.mean(self._history[subject_id], axis=0)
            values = {item: float(probabilities[index]) for index, item in enumerate(PPE_ITEMS)}
            flags = {item: values[item] >= self.item_thresholds[item] for item in PPE_ITEMS}
            missing = tuple(item for item in PPE_ITEMS if not flags[item])
            level = SafetyLevel.WARNING if missing else SafetyLevel.SAFE
            observation = DetectionObservation(
                camera_id=camera_id,
                detector=DETECTOR_NAME,
                subject_id=subject_id,
                subject_kind=SubjectKind.PERSON,
                level=level,
                timestamp=float(timestamp),
                confidence=min(values.values()),
                box=box,
                metadata={"flags": flags, "probabilities": values, "missing_items": list(missing)},
            )
            detections.append(PPEDetection(observation, flags, values, missing))

        for subject_id in list(self._history):
            if subject_id not in live_ids:
                del self._history[subject_id]
        return detections

    @staticmethod
    def draw_overlay(frame, detections, confirmed_levels=None):
        import cv2

        output = frame.copy()
        scale = max(0.75, min(1.25, output.shape[1] / 1280.0))
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
            thickness = max(2, int(3 * scale))
            cv2.rectangle(output, (x1, y1), (x2, y2), color, thickness)

            font_scale = 0.55 * scale
            row_height = int(26 * scale)
            panel_width = int(230 * scale)
            panel_height = row_height * (len(PPE_ITEMS) + 1) + int(10 * scale)
            panel_x = min(x1, max(0, output.shape[1] - panel_width - 1))
            panel_y = max(0, y1 - panel_height)

            panel = output.copy()
            cv2.rectangle(
                panel,
                (panel_x, panel_y),
                (panel_x + panel_width, panel_y + panel_height),
                (24, 24, 28),
                -1,
            )
            cv2.addWeighted(panel, 0.72, output, 0.28, 0, output)
            cv2.rectangle(
                output,
                (panel_x, panel_y),
                (panel_x + panel_width, panel_y + panel_height),
                color,
                max(1, int(2 * scale)),
            )
            cv2.putText(
                output,
                f"PERSON {observation.subject_id}  {level.value}",
                (panel_x + int(8 * scale), panel_y + row_height - int(8 * scale)),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                color,
                max(1, int(2 * scale)),
                cv2.LINE_AA,
            )
            for index, item in enumerate(PPE_ITEMS):
                row_y = panel_y + row_height * (index + 2) - int(8 * scale)
                present = detection.flags[item]
                item_color = (0, 210, 0) if present else (0, 0, 255)
                cv2.putText(
                    output, item.upper(), (panel_x + int(8 * scale), row_y),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (245, 245, 245),
                    max(1, int(2 * scale)), cv2.LINE_AA,
                )
                cv2.putText(
                    output, "YES" if present else "NO",
                    (panel_x + int(105 * scale), row_y),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, item_color,
                    max(1, int(2 * scale)), cv2.LINE_AA,
                )
                cv2.putText(
                    output, f"{detection.probabilities[item]:.2f}",
                    (panel_x + int(165 * scale), row_y),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (245, 245, 245),
                    max(1, int(2 * scale)), cv2.LINE_AA,
                )
        return output
