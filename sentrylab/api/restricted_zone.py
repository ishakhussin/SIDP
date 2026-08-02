"""Restricted-zone configuration endpoints."""

from flask import Blueprint, current_app, jsonify, request

from sentrylab.database.zones import RESTRICTED_ZONE_DETECTOR


restricted_zone_blueprint = Blueprint("restricted_zone", __name__)


def _known_camera(camera_id: str) -> bool:
    try:
        current_app.extensions["camera_manager"].status(camera_id)
        return True
    except KeyError:
        return False


@restricted_zone_blueprint.route(
    "/api/cameras/<path:camera_id>/restricted-zone", methods=["GET", "PUT"]
)
def restricted_zone(camera_id: str):
    if not _known_camera(camera_id):
        return jsonify({"error": "Camera not found"}), 404
    preset = str(request.args.get("preset", "HOME")).strip().upper()
    if not preset or len(preset) > 40:
        return jsonify({"error": "Invalid PTZ preset name"}), 400

    repository = current_app.extensions["zone_repository"]
    if request.method == "GET":
        zone = repository.get(camera_id, preset)
        return jsonify(zone or {
            "camera_id": camera_id,
            "preset_name": preset,
            "points": [],
            "updated_at": None,
        })

    payload = request.get_json(silent=True) or {}
    try:
        zone = repository.save(camera_id, preset, payload.get("points"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(zone)


@restricted_zone_blueprint.route(
    "/api/cameras/<path:camera_id>/detectors/restricted-zone",
    methods=["GET", "PUT"],
)
def restricted_zone_settings(camera_id: str):
    if not _known_camera(camera_id):
        return jsonify({"error": "Camera not found"}), 404
    repository = current_app.extensions["detector_settings_repository"]
    if request.method == "GET":
        return jsonify(repository.get(camera_id, RESTRICTED_ZONE_DETECTOR))

    payload = request.get_json(silent=True) or {}
    if "enabled" not in payload or "overlay_enabled" not in payload:
        return jsonify({"error": "enabled and overlay_enabled are required"}), 400
    if not isinstance(payload["enabled"], bool) or not isinstance(
        payload["overlay_enabled"], bool
    ):
        return jsonify({"error": "enabled and overlay_enabled must be booleans"}), 400
    settings = repository.save(
        camera_id,
        RESTRICTED_ZONE_DETECTOR,
        payload["enabled"],
        payload["overlay_enabled"],
    )
    current_app.extensions["detection_manager"].apply_settings(
        camera_id, RESTRICTED_ZONE_DETECTOR
    )
    return jsonify(settings)


@restricted_zone_blueprint.get(
    "/api/cameras/<path:camera_id>/detectors/restricted-zone/status"
)
def restricted_zone_status(camera_id: str):
    if not _known_camera(camera_id):
        return jsonify({"error": "Camera not found"}), 404
    return jsonify(current_app.extensions["detection_manager"].status(
        camera_id, RESTRICTED_ZONE_DETECTOR
    ))
