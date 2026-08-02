"""Asynchronous three-second pre-roll and seven-second post-roll clips."""

from __future__ import annotations

import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BufferedFrame:
    timestamp: float
    jpeg: bytes


@dataclass
class RecordingJob:
    incident_id: int
    unsafe_at: float
    end_at: float
    overlay_enabled: bool
    frames: list[BufferedFrame]


class EvidenceClipRecorder:
    """Keeps compressed pre-roll frames and writes completed clips off-thread."""

    def __init__(
        self,
        camera_id: str,
        detector: str,
        clips_dir: Path,
        incident_repository,
        fps: float = 10.0,
        pre_seconds: float = 3.0,
        post_seconds: float = 7.0,
        frame_encoder=None,
        clip_writer=None,
    ) -> None:
        self.camera_id = camera_id
        self.detector = detector
        self.clips_dir = Path(clips_dir)
        self.incident_repository = incident_repository
        self.fps = max(1.0, float(fps))
        self.pre_seconds = max(0.0, float(pre_seconds))
        self.post_seconds = max(0.0, float(post_seconds))
        self.frame_encoder = frame_encoder or self._encode_jpeg
        self.clip_writer = clip_writer or self._write_mp4

        self._buffer = deque()
        self._jobs: dict[int, RecordingJob] = {}
        self._known_incidents: set[int] = set()
        self._last_sample_at: float | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"clip-{camera_id.replace(' ', '-').lower()}",
        )
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._clips_written = 0

    def add_frame(self, frame, timestamp: float) -> bool:
        timestamp = float(timestamp)
        if (
            self._last_sample_at is not None
            and timestamp - self._last_sample_at < 1.0 / self.fps
        ):
            self.poll(timestamp)
            return False

        try:
            encoded = self.frame_encoder(frame)
        except Exception as error:
            self._set_error(error)
            self.poll(timestamp)
            return False
        if not encoded:
            self._set_error(RuntimeError("Evidence frame encoding failed"))
            self.poll(timestamp)
            return False

        sample = BufferedFrame(timestamp, bytes(encoded))
        self._last_sample_at = timestamp
        self._buffer.append(sample)
        cutoff = timestamp - self.pre_seconds
        while self._buffer and self._buffer[0].timestamp < cutoff:
            self._buffer.popleft()
        for job in self._jobs.values():
            if timestamp <= job.end_at:
                job.frames.append(sample)
        self.poll(timestamp)
        return True

    def trigger(
        self,
        incident_id: int,
        unsafe_at: float,
        overlay_enabled: bool,
    ) -> bool:
        incident_id = int(incident_id)
        if incident_id in self._known_incidents:
            return False
        self._known_incidents.add(incident_id)
        unsafe_at = float(unsafe_at)
        self._jobs[incident_id] = RecordingJob(
            incident_id=incident_id,
            unsafe_at=unsafe_at,
            end_at=unsafe_at + self.post_seconds,
            overlay_enabled=bool(overlay_enabled),
            frames=list(self._buffer),
        )
        return True

    def poll(self, now: float) -> None:
        completed = [
            job for job in self._jobs.values() if float(now) >= job.end_at
        ]
        for job in completed:
            self._jobs.pop(job.incident_id, None)
            self._executor.submit(self._persist, job)

    def stop(self) -> None:
        for job in list(self._jobs.values()):
            self._jobs.pop(job.incident_id, None)
            self._executor.submit(self._persist, job)
        self._executor.shutdown(wait=True, cancel_futures=False)

    def status(self) -> dict:
        with self._lock:
            return {
                "active_recordings": len(self._jobs),
                "clips_written": self._clips_written,
                "last_clip_error": self._last_error,
                "buffered_frames": len(self._buffer),
            }

    def record_error(self, error: Exception) -> None:
        self._set_error(error)

    def _persist(self, job: RecordingJob) -> None:
        safe_camera = self.camera_id.lower().replace(" ", "-")
        safe_detector = self.detector.lower().replace("_", "-")
        filename = (
            f"incident-{job.incident_id:06d}_{safe_camera}_{safe_detector}-h264.mp4"
        )
        path = self.clips_dir / filename
        temporary_path = path.with_name(f"{path.stem}.partial{path.suffix}")
        try:
            self.clips_dir.mkdir(parents=True, exist_ok=True)
            start_at = job.unsafe_at - self.pre_seconds
            if not self.clip_writer(
                temporary_path, job.frames, start_at, job.end_at, self.fps
            ):
                raise RuntimeError("Video writer could not create a playable clip")
            if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                raise RuntimeError("Video writer produced an empty clip")
            temporary_path.replace(path)
            self.incident_repository.set_clip(
                job.incident_id, filename, job.overlay_enabled
            )
            with self._lock:
                self._clips_written += 1
                self._last_error = None
        except Exception as error:
            try:
                for failed_path in (temporary_path, path):
                    if failed_path.is_file():
                        failed_path.unlink()
            except OSError:
                LOGGER.exception("Could not remove failed evidence clip %s", path)
            self._set_error(error)
            LOGGER.exception("Evidence clip failed for incident %s", job.incident_id)

    def _set_error(self, error: Exception) -> None:
        with self._lock:
            self._last_error = str(error)

    @staticmethod
    def _encode_jpeg(frame) -> bytes:
        import cv2

        ok, encoded = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85]
        )
        if not ok:
            raise RuntimeError("OpenCV could not encode evidence frame")
        return encoded.tobytes()

    @staticmethod
    def _write_mp4(
        path: Path,
        frames: list[BufferedFrame],
        start_at: float,
        end_at: float,
        fps: float,
    ) -> bool:
        import cv2
        import numpy as np

        ordered = sorted(frames, key=lambda item: item.timestamp)
        if not ordered:
            return False
        first = cv2.imdecode(np.frombuffer(ordered[0].jpeg, np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            return False
        height, width = first.shape[:2]
        # Chromium browsers do not reliably support OpenCV's historical mp4v
        # output. On Windows, Media Foundation provides a fast native H.264
        # encoder whose avc1 stream plays both in browsers and desktop players.
        writer = cv2.VideoWriter(
            str(path),
            cv2.CAP_MSMF,
            cv2.VideoWriter_fourcc(*"avc1"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            writer.release()
            return False

        frame_count = max(1, round((end_at - start_at) * fps))
        source_index = 0
        try:
            for output_index in range(frame_count):
                target_at = start_at + output_index / fps
                while (
                    source_index + 1 < len(ordered)
                    and ordered[source_index + 1].timestamp <= target_at
                ):
                    source_index += 1
                decoded = cv2.imdecode(
                    np.frombuffer(ordered[source_index].jpeg, np.uint8),
                    cv2.IMREAD_COLOR,
                )
                if decoded is None:
                    continue
                if decoded.shape[1] != width or decoded.shape[0] != height:
                    decoded = cv2.resize(decoded, (width, height))
                writer.write(decoded)
        finally:
            writer.release()
        return path.is_file() and path.stat().st_size > 0
