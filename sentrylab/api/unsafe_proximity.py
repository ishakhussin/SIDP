"""Unsafe Proximity settings and runtime status endpoints."""

from flask import Blueprint, current_app, jsonify, request

from sentrylab.database.zones import UNSAFE_PROXIMITY_DETECTOR


unsafe_proximity_blueprint = Blueprint("unsafe_proximity", __name__)


def _known_camera(camera_id: str) -> bool:
    try:
        current_app.extensions["camera_manager"].status(camera_id)
        return True
    except KeyError:
        return False


@unsafe_proximity_blueprint.route(
    "/api/cameras/<path:camera_id>/detectors/unsafe-proximity",
    methods=["GET", "PUT"],
)
def unsafe_proximity_settings(camera_id: str):
    if not _known_camera(camera_id):
        return jsonify({"error": "Camera not found"}), 404
    repository = current_app.extensions["detector_settings_repository"]
    if request.method == "GET":
        return jsonify(repository.get(camera_id, UNSAFE_PROXIMITY_DETECTOR))

    payload = request.get_json(silent=True) or {}
    if "enabled" not in payload or "overlay_enabled" not in payload:
        return jsonify({"error": "enabled and overlay_enabled are required"}), 400
    if not isinstance(payload["enabled"], bool) or not isinstance(
        payload["overlay_enabled"], bool
    ):
        return jsonify({"error": "enabled and overlay_enabled must be booleans"}), 400
    settings = repository.save(
        camera_id,
        UNSAFE_PROXIMITY_DETECTOR,
        payload["enabled"],
        payload["overlay_enabled"],
    )
    current_app.extensions["detection_manager"].apply_settings(
        camera_id, UNSAFE_PROXIMITY_DETECTOR
    )
    return jsonify(settings)


@unsafe_proximity_blueprint.get(
    "/api/cameras/<path:camera_id>/detectors/unsafe-proximity/status"
)
def unsafe_proximity_status(camera_id: str):
    if not _known_camera(camera_id):
        return jsonify({"error": "Camera not found"}), 404
    return jsonify(current_app.extensions["detection_manager"].status(
        camera_id, UNSAFE_PROXIMITY_DETECTOR
    ))
