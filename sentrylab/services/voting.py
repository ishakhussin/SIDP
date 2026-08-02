"""Shared five-second confirmation logic for every detector and subject."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sentrylab.domain.detection import SafetyLevel


@dataclass
class MajorityVoteState:
    window_size: int = 5
    required_votes: int = 3
    missing_timeout_seconds: float = 2.0
    level: SafetyLevel = SafetyLevel.SAFE
    last_seen_at: float | None = None
    votes: deque[SafetyLevel] = field(default_factory=lambda: deque(maxlen=5))

    def __post_init__(self) -> None:
        if self.window_size < 1:
            raise ValueError("window_size must be positive")
        if not 1 <= self.required_votes <= self.window_size:
            raise ValueError("required_votes must fit inside the window")
        if self.votes.maxlen != self.window_size:
            self.votes = deque(self.votes, maxlen=self.window_size)

    def add_vote(self, vote: SafetyLevel, timestamp: float) -> SafetyLevel:
        """Add one valid one-second sample to the current decision window.

        A SAFE subject does not accumulate historical votes. Its first raw
        violation changes the visible level to WARNING immediately, clears any
        history, and starts a fresh five-sample window beginning with the next
        one-second check. UNKNOWN samples never advance a window.
        """
        self.last_seen_at = float(timestamp)
        if vote is SafetyLevel.UNKNOWN:
            # An unavailable/expired AI result must never count as either a
            # SAFE or WARNING sample and must not advance the five-vote window.
            return self.level
        if vote is SafetyLevel.UNSAFE:
            # Detectors report raw violation votes as WARNING. UNSAFE is owned
            # by this confirmation service, not individual model adapters.
            vote = SafetyLevel.WARNING

        if self.level is SafetyLevel.SAFE:
            self.votes.clear()
            if vote is SafetyLevel.WARNING:
                # This transition is immediate, but it is not one of the five
                # subsequent decision samples. Continuous violation therefore
                # reaches UNSAFE five seconds after WARNING begins.
                self.level = SafetyLevel.WARNING
            return self.level

        self.votes.append(vote)
        if len(self.votes) < self.window_size:
            return self.level

        warning_votes = self.votes.count(SafetyLevel.WARNING)
        safe_votes = self.votes.count(SafetyLevel.SAFE)
        if self.level is SafetyLevel.WARNING:
            self.level = (
                SafetyLevel.UNSAFE
                if warning_votes >= self.required_votes
                else SafetyLevel.SAFE
            )
        elif self.level is SafetyLevel.UNSAFE and safe_votes >= self.required_votes:
            self.level = SafetyLevel.SAFE

        # Every decision uses a discrete five-sample window. If the subject
        # remains UNSAFE, the next five valid samples form a fresh recovery
        # window rather than reusing historical votes.
        self.votes.clear()
        return self.level

    def is_expired(self, now: float) -> bool:
        if self.last_seen_at is None:
            return False
        return float(now) - self.last_seen_at > self.missing_timeout_seconds

    def expire(self) -> None:
        self.votes.clear()
        self.level = SafetyLevel.SAFE
        self.last_seen_at = None
