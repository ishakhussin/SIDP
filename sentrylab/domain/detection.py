"""Model-independent detection result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SafetyLevel(StrEnum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


class SubjectKind(StrEnum):
    PERSON = "PERSON"
    PAIR = "PAIR"


@dataclass(frozen=True)
class DetectionObservation:
    camera_id: str
    detector: str
    subject_id: str
    subject_kind: SubjectKind
    level: SafetyLevel
    timestamp: float
    confidence: float | None = None
    box: tuple[int, int, int, int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
