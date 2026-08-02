"""Fast, model-independent validation of the local AI model bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelGroup:
    detector: str
    label: str
    files: tuple[str, ...]


MODEL_GROUPS = (
    ModelGroup(
        "restricted_zone",
        "Restricted Zone",
        ("restricted_zone/yolo11n-pose.pt",),
    ),
    ModelGroup(
        "unsafe_proximity",
        "Unsafe Proximity",
        (
            "unsafe_proximity/yolo11n.pt",
            "unsafe_proximity/config.json",
            "unsafe_proximity/preprocessor_config.json",
            "unsafe_proximity/model.safetensors",
        ),
    ),
    ModelGroup(
        "ppe_compliance",
        "PPE Compliance",
        ("ppe/yolov8n.pt", "ppe/ppe_multilabel_best.pt"),
    ),
)


class ModelInventory:
    """Reports model availability without importing an AI framework."""

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir)

    def status(self) -> dict:
        groups = []
        total_missing = 0
        for group in MODEL_GROUPS:
            files = []
            missing = []
            for relative_path in group.files:
                path = self.model_dir / Path(relative_path)
                present = path.is_file() and path.stat().st_size > 0
                item = {
                    "path": relative_path,
                    "present": present,
                    "size_bytes": path.stat().st_size if present else 0,
                }
                files.append(item)
                if not present:
                    missing.append(relative_path)
            total_missing += len(missing)
            groups.append(
                {
                    "detector": group.detector,
                    "label": group.label,
                    "ready": not missing,
                    "files": files,
                    "missing": missing,
                }
            )
        return {
            "ready": total_missing == 0,
            "missing_count": total_missing,
            "groups": groups,
        }
