"""Shared per-subject voting and incident-transition processing."""

from __future__ import annotations

from dataclasses import replace

from sentrylab.domain.detection import SafetyLevel
from sentrylab.services.voting import MajorityVoteState


class IncidentVoteProcessor:
    """Turn raw one-second observations into confirmed incident levels."""

    def __init__(
        self,
        incident_repository,
        vote_interval_seconds: float = 1.0,
        identity_handoff_seconds: float = 2.0,
        identity_iou_threshold: float = 0.20,
    ) -> None:
        self.incident_repository = incident_repository
        self.vote_interval_seconds = float(vote_interval_seconds)
        self.identity_handoff_seconds = float(identity_handoff_seconds)
        self.identity_iou_threshold = float(identity_iou_threshold)
        self._voters = {}
        self._aliases = {}
        self._last_vote_at = {}
        self._last_observed_at = {}
        self._reported_level = {}
        self._latest_observation = {}
        self._new_unsafe_incident_ids = []

    def process(self, detections, timestamp: float) -> dict[str, SafetyLevel]:
        output_levels = {}
        used_canonical_ids = set()
        for detection in detections:
            raw = detection.observation
            visible_subject_id = raw.subject_id
            subject_id = self._resolve_identity(
                raw, timestamp, used_canonical_ids
            )
            used_canonical_ids.add(subject_id)
            canonical_raw = (
                raw if subject_id == visible_subject_id
                else replace(raw, subject_id=subject_id)
            )
            self._last_observed_at[subject_id] = timestamp
            self._latest_observation[subject_id] = canonical_raw
            voter = self._voters.setdefault(subject_id, MajorityVoteState())

            last_vote = self._last_vote_at.get(subject_id)
            immediate_warning = (
                canonical_raw.level is SafetyLevel.WARNING
                and voter.level is SafetyLevel.SAFE
            )
            should_vote = (
                last_vote is None
                or timestamp - last_vote >= self.vote_interval_seconds
                or immediate_warning
            )
            if not should_vote:
                output_levels[visible_subject_id] = voter.level
                continue

            confirmed = voter.add_vote(canonical_raw.level, timestamp)
            self._last_vote_at[subject_id] = timestamp
            if self._reported_level.get(subject_id) != confirmed:
                incident = self.incident_repository.record_observation(
                    replace(canonical_raw, level=confirmed, timestamp=timestamp)
                )
                self._reported_level[subject_id] = confirmed
                if confirmed is SafetyLevel.UNSAFE and incident is not None:
                    incident_id = int(incident["id"])
                    if incident_id not in self._new_unsafe_incident_ids:
                        self._new_unsafe_incident_ids.append(incident_id)
            output_levels[visible_subject_id] = voter.level

        for subject_id in list(self._voters):
            last_seen = self._last_observed_at.get(subject_id, timestamp)
            voter = self._voters[subject_id]
            if timestamp - last_seen <= voter.missing_timeout_seconds:
                continue
            previous = self._reported_level.get(subject_id, SafetyLevel.SAFE)
            if previous in {SafetyLevel.WARNING, SafetyLevel.UNSAFE}:
                latest = self._latest_observation[subject_id]
                self.incident_repository.record_observation(replace(
                    latest,
                    level=SafetyLevel.SAFE,
                    timestamp=timestamp,
                    metadata={**latest.metadata, "close_reason": "subject left frame"},
                ))
            self._voters.pop(subject_id, None)
            self._last_vote_at.pop(subject_id, None)
            self._last_observed_at.pop(subject_id, None)
            self._reported_level.pop(subject_id, None)
            self._latest_observation.pop(subject_id, None)
            for alias, canonical in list(self._aliases.items()):
                if canonical == subject_id:
                    self._aliases.pop(alias, None)

        return output_levels

    def _resolve_identity(self, observation, timestamp: float, used_ids: set[str]) -> str:
        visible_id = observation.subject_id
        existing = self._aliases.get(visible_id, visible_id)
        if existing in self._voters and existing not in used_ids:
            self._aliases[visible_id] = existing
            return existing

        best_id = None
        best_score = self.identity_iou_threshold
        for candidate_id, candidate in self._latest_observation.items():
            if candidate_id in used_ids or candidate.subject_kind != observation.subject_kind:
                continue
            last_seen = self._last_observed_at.get(candidate_id)
            if last_seen is None or timestamp - last_seen > self.identity_handoff_seconds:
                continue
            score = self._spatial_similarity(candidate, observation)
            if score >= best_score:
                best_id, best_score = candidate_id, score

        canonical = best_id or visible_id
        self._aliases[visible_id] = canonical
        return canonical

    @classmethod
    def _spatial_similarity(cls, previous, current) -> float:
        if previous.subject_kind.value == "PAIR":
            old_boxes = previous.metadata.get("boxes") or []
            new_boxes = current.metadata.get("boxes") or []
            if len(old_boxes) != 2 or len(new_boxes) != 2:
                return 0.0
            direct = (
                cls._box_iou(old_boxes[0], new_boxes[0])
                + cls._box_iou(old_boxes[1], new_boxes[1])
            ) / 2.0
            swapped = (
                cls._box_iou(old_boxes[0], new_boxes[1])
                + cls._box_iou(old_boxes[1], new_boxes[0])
            ) / 2.0
            return max(direct, swapped)
        if previous.box is None or current.box is None:
            return 0.0
        return cls._box_iou(previous.box, current.box)

    @staticmethod
    def _box_iou(first, second) -> float:
        ax1, ay1, ax2, ay2 = [float(value) for value in first]
        bx1, by1, bx2, by2 = [float(value) for value in second]
        intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
        intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
        intersection = intersection_width * intersection_height
        first_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        second_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = first_area + second_area - intersection
        return intersection / union if union > 0 else 0.0

    def take_new_unsafe_incident_ids(self) -> list[int]:
        incident_ids = list(self._new_unsafe_incident_ids)
        self._new_unsafe_incident_ids.clear()
        return incident_ids

    def close_all(self, timestamp: float, reason: str) -> None:
        for subject_id, previous in list(self._reported_level.items()):
            if previous in {SafetyLevel.WARNING, SafetyLevel.UNSAFE}:
                latest = self._latest_observation[subject_id]
                self.incident_repository.record_observation(replace(
                    latest,
                    level=SafetyLevel.SAFE,
                    timestamp=timestamp,
                    metadata={**latest.metadata, "close_reason": reason},
                ))
        self._voters.clear()
        self._aliases.clear()
        self._last_vote_at.clear()
        self._last_observed_at.clear()
        self._reported_level.clear()
        self._latest_observation.clear()
        self._new_unsafe_incident_ids.clear()
