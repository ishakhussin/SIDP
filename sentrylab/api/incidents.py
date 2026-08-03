"""Incident queries, deletion, and local export endpoints."""

from __future__ import annotations

import csv
import io
import logging
import threading
import zipfile
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, send_file


incidents_blueprint = Blueprint("incidents", __name__)
LOGGER = logging.getLogger(__name__)
_browser_clip_lock = threading.Lock()
CSV_FIELDS = (
    "id",
    "camera_id",
    "detector",
    "current_level",
    "opened_at",
    "unsafe_at",
    "closed_at",
    "close_reason",
    "clip_path",
    "overlay_enabled",
)
LOG_CSV_FIELDS = (
    "entry_id", "timestamp", "camera_id", "level", "message",
    "detector", "incident_id", "people_count",
)
DETECTOR_LABELS = {
    "restricted_zone": "Restricted Zone",
    "unsafe_proximity": "Unsafe Proximity",
    "ppe_compliance": "PPE Compliance",
}


def _repository():
    return current_app.extensions["incident_repository"]


def _settings():
    return current_app.config["SENTRYLAB_SETTINGS"]


def _monitoring_repository():
    return current_app.extensions["monitoring_log_repository"]


def _safe_clip_path(relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    root = _settings().clips_dir.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _browser_cache_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}.browser-h264.mp4")


def _transcode_browser_clip(source: Path, target: Path) -> bool:
    """Convert a legacy mp4v clip to browser-compatible H.264 on Windows."""
    import cv2

    capture = cv2.VideoCapture(str(source))
    temporary = target.with_name(f"{target.stem}.partial.mp4")
    writer = None
    try:
        if not capture.isOpened():
            return False
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 10.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            return False
        writer = cv2.VideoWriter(
            str(temporary),
            cv2.CAP_MSMF,
            cv2.VideoWriter_fourcc(*"avc1"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            return False
        frames_written = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            writer.write(frame)
            frames_written += 1
        writer.release()
        writer = None
        if frames_written == 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            return False
        temporary.replace(target)
        return True
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if temporary.is_file():
            temporary.unlink()


def _browser_clip(source: Path) -> Path | None:
    if source.name.endswith("-h264.mp4"):
        return source
    cached = _browser_cache_path(source)
    with _browser_clip_lock:
        if cached.is_file() and cached.stat().st_mtime >= source.stat().st_mtime:
            return cached
        try:
            if _transcode_browser_clip(source, cached):
                return cached
        except Exception:
            LOGGER.exception("Could not create browser clip for %s", source)
    return None


def _csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _log_entries(camera_id: str | None = None, limit: int = 500) -> list[dict]:
    incidents = _repository().list(camera_id=camera_id, limit=limit)
    safe_logs = _monitoring_repository().list(camera_id=camera_id, limit=limit)
    entries = []
    for incident in incidents:
        detector = incident["detector"]
        label = DETECTOR_LABELS.get(detector, detector.replace("_", " ").title())
        common = {
            "camera_id": incident["camera_id"],
            "detector": detector,
            "incident_id": incident["id"],
            "people_count": None,
            "clip_path": incident.get("clip_path"),
        }
        entries.append({
            **common,
            "entry_id": f"warning-{incident['id']}",
            "timestamp": incident["opened_at"],
            "level": "WARNING",
            "message": f"{label} warning detected",
        })
        if incident.get("unsafe_at") is not None:
            entries.append({
                **common,
                "entry_id": f"unsafe-{incident['id']}",
                "timestamp": incident["unsafe_at"],
                "level": "UNSAFE",
                "message": f"{label} violation confirmed",
            })
        if (
            incident.get("closed_at") is not None
            and incident.get("close_reason") in {
                "all subjects recovered", "subject left frame",
            }
        ):
            message = (
                "Person left camera frame"
                if incident["close_reason"] == "subject left frame"
                else "Returned to a safe condition"
            )
            entries.append({
                **common,
                "entry_id": f"safe-{incident['id']}",
                "timestamp": incident["closed_at"],
                "level": "SAFE",
                "message": message,
            })
    entries.extend({
        "entry_id": f"heartbeat-{row['id']}",
        "timestamp": row["created_at"],
        "camera_id": row["camera_id"],
        "level": "SAFE",
        "message": row["message"],
        "detector": "monitoring",
        "incident_id": None,
        "people_count": row["people_count"],
        "clip_path": None,
    } for row in safe_logs)
    entries.sort(key=lambda item: (float(item["timestamp"]), item["entry_id"]), reverse=True)
    return entries[:max(1, min(int(limit), 1000))]


@incidents_blueprint.get("/api/incidents")
def list_incidents():
    rows = _repository().list(
        camera_id=request.args.get("camera_id") or None,
        detector=request.args.get("detector") or None,
        status=request.args.get("status") or None,
        limit=request.args.get("limit", 100, type=int),
        offset=request.args.get("offset", 0, type=int),
    )
    return jsonify({"incidents": rows, "count": len(rows)})


@incidents_blueprint.get("/api/dashboard/summary")
def dashboard_summary():
    camera_id = request.args.get("camera_id") or None
    summary = _repository().summary(
        camera_id=camera_id,
        recent_limit=request.args.get("recent_limit", 5, type=int),
    )
    safe_heartbeats = _monitoring_repository().count(camera_id=camera_id)
    summary["safe"] += safe_heartbeats
    summary["total_events"] += safe_heartbeats
    return jsonify(summary)


@incidents_blueprint.get("/api/log-entries")
def list_log_entries():
    entries = _log_entries(
        camera_id=request.args.get("camera_id") or None,
        limit=request.args.get("limit", 500, type=int),
    )
    level = (request.args.get("level") or "").upper()
    if level:
        entries = [entry for entry in entries if entry["level"] == level]
    return jsonify({"entries": entries, "count": len(entries)})


@incidents_blueprint.get("/api/log-entries/export.csv")
def export_log_entries_csv():
    entries = _log_entries(
        camera_id=request.args.get("camera_id") or None,
        limit=1000,
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=LOG_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(entries)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sentrylab-log.csv"},
    )


@incidents_blueprint.get("/api/incidents/export.csv")
def export_incidents_csv():
    rows = _repository().list(
        camera_id=request.args.get("camera_id") or None,
        detector=request.args.get("detector") or None,
        status=request.args.get("status") or None,
        limit=500,
    )
    return Response(
        _csv_bytes(rows),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sentrylab-incidents.csv"},
    )


@incidents_blueprint.get("/api/incidents/export.zip")
def export_incidents_zip():
    raw_ids = request.args.get("ids", "")
    try:
        ids = [int(value) for value in raw_ids.split(",") if value.strip()]
    except ValueError:
        return jsonify({"error": "ids must be comma-separated integers"}), 400
    if not ids:
        return jsonify({"error": "At least one incident ID is required"}), 400
    if len(ids) > 100:
        return jsonify({"error": "A maximum of 100 incidents can be exported"}), 400

    rows = []
    for incident_id in dict.fromkeys(ids):
        incident = _repository().get(incident_id)
        if incident is not None:
            rows.append(incident)

    archive_buffer = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("incidents.csv", _csv_bytes(rows))
        for row in rows:
            clip = _safe_clip_path(row.get("clip_path"))
            if clip is None or not clip.is_file():
                continue
            archive_name = f"clips/{clip.name}"
            if archive_name in used_names:
                archive_name = f"clips/{row['id']}_{clip.name}"
            used_names.add(archive_name)
            archive.write(clip, archive_name)
    archive_buffer.seek(0)
    return send_file(
        archive_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="sentrylab-incidents.zip",
    )


@incidents_blueprint.get("/api/incidents/<int:incident_id>")
def incident_detail(incident_id: int):
    incident = _repository().get(incident_id)
    if incident is None:
        return jsonify({"error": "Incident not found"}), 404
    return jsonify(incident)


@incidents_blueprint.get("/api/incidents/<int:incident_id>/clip")
def incident_clip(incident_id: int):
    incident = _repository().get(incident_id)
    if incident is None:
        return jsonify({"error": "Incident not found"}), 404
    clip = _safe_clip_path(incident.get("clip_path"))
    if clip is None or not clip.is_file():
        return jsonify({"error": "Evidence clip is not ready"}), 404
    return send_file(
        clip,
        mimetype="video/mp4",
        as_attachment=False,
        conditional=True,
        download_name=clip.name,
    )


@incidents_blueprint.get("/api/incidents/<int:incident_id>/browser-clip")
def incident_browser_clip(incident_id: int):
    incident = _repository().get(incident_id)
    if incident is None:
        return jsonify({"error": "Incident not found"}), 404
    source = _safe_clip_path(incident.get("clip_path"))
    if source is None or not source.is_file():
        return jsonify({"error": "Evidence clip is not ready"}), 404
    clip = _browser_clip(source)
    if clip is None:
        return jsonify({"error": "Evidence clip could not be prepared for browser playback"}), 500
    return send_file(
        clip,
        mimetype="video/mp4",
        as_attachment=False,
        conditional=True,
        download_name=clip.name,
    )


@incidents_blueprint.delete("/api/incidents/<int:incident_id>")
def delete_incident(incident_id: int):
    incident = _repository().get(incident_id)
    if incident is None:
        return jsonify({"error": "Incident not found"}), 404
    if incident["closed_at"] is None:
        return jsonify({"error": "An active incident cannot be deleted"}), 409

    deleted = _repository().delete(incident_id)
    clip = _safe_clip_path(deleted.get("clip_path")) if deleted else None
    clip_deleted = False
    if clip is not None and clip.is_file():
        browser_cache = _browser_cache_path(clip)
        clip.unlink()
        if browser_cache.is_file():
            browser_cache.unlink()
        clip_deleted = True
    return jsonify({"ok": True, "incident_id": incident_id, "clip_deleted": clip_deleted})
