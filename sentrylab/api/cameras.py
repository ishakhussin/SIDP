"""Read-only camera status endpoints."""

from flask import Blueprint, Response, current_app, jsonify, request

from sentrylab.streaming import encode_jpeg, mjpeg_generator
from sentrylab.services.ptz import PtzError


cameras_blueprint = Blueprint("cameras", __name__)


def _manager():
    return current_app.extensions["camera_manager"]


def _ptz():
    return current_app.extensions["ptz_controller"]


def _render(camera_id: str, frame):
    return current_app.extensions["detection_manager"].render(camera_id, frame)


@cameras_blueprint.get("/api/cameras")
def list_cameras():
    return jsonify({"cameras": _manager().statuses()})


@cameras_blueprint.get("/api/cameras/<path:camera_id>/status")
def camera_status(camera_id: str):
    try:
        return jsonify(_manager().status(camera_id))
    except KeyError:
        return jsonify({"error": "Camera not found"}), 404


@cameras_blueprint.get("/api/cameras/<path:camera_id>/controls")
def camera_controls(camera_id: str):
    try:
        _manager().status(camera_id)
    except KeyError:
        return jsonify({"error": "Camera not found"}), 404
    return jsonify(_ptz().capabilities(camera_id))


@cameras_blueprint.post("/api/cameras/<path:camera_id>/ptz")
def camera_ptz(camera_id: str):
    if camera_id != "CAM 01":
        return jsonify({"error": "Physical pan and tilt are only available on CAM 01"}), 409
    action = str((request.get_json(silent=True) or {}).get("action", "")).lower()
    try:
        result = _ptz().home() if action == "home" else _ptz().move(action)
        return jsonify(result)
    except PtzError as error:
        return jsonify({"error": str(error)}), 502


@cameras_blueprint.get("/api/cameras/<path:camera_id>/presets")
def camera_presets(camera_id: str):
    if camera_id != "CAM 01":
        return jsonify({"error": "Presets are only available on CAM 01"}), 409
    try:
        return jsonify({"presets": _ptz().preset_status()})
    except PtzError as error:
        return jsonify({"error": str(error)}), 502


@cameras_blueprint.post("/api/cameras/<path:camera_id>/presets/<slot>")
def camera_preset_action(camera_id: str, slot: str):
    if camera_id != "CAM 01":
        return jsonify({"error": "Presets are only available on CAM 01"}), 409
    action = str((request.get_json(silent=True) or {}).get("action", "goto")).lower()
    try:
        result = _ptz().save_preset(slot) if action == "save" else _ptz().goto_preset(slot)
        return jsonify(result)
    except PtzError as error:
        return jsonify({"error": str(error)}), 502


@cameras_blueprint.post("/api/cameras/<path:camera_id>/patrol")
def camera_patrol(camera_id: str):
    if camera_id != "CAM 01":
        return jsonify({"error": "Auto Patrol is only available on CAM 01"}), 409
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be a boolean"}), 400
    try:
        result = _ptz().start_patrol() if enabled else _ptz().stop_patrol()
        return jsonify(result)
    except PtzError as error:
        return jsonify({"error": str(error)}), 409


@cameras_blueprint.put("/api/cameras/<path:camera_id>/power")
def camera_power(camera_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("on"), bool):
        return jsonify({"error": "on must be a boolean"}), 400
    detection_manager = current_app.extensions["detection_manager"]
    try:
        if payload["on"]:
            camera = _manager().start_camera(camera_id)
            detection_manager.apply_settings(camera_id)
        else:
            if camera_id == "CAM 01":
                _ptz().stop_patrol()
            detection_manager.stop_camera(camera_id)
            camera = _manager().stop_camera(camera_id)
    except KeyError:
        return jsonify({"error": "Camera not found"}), 404
    except ValueError as error:
        return jsonify({"error": str(error)}), 409
    return jsonify({"power_on": camera["power_on"], "camera": camera})


def _stream_worker(camera_id: str):
    try:
        worker = _manager().existing_worker(camera_id)
    except KeyError:
        return None, (jsonify({"error": "Camera not found"}), 404)
    if worker is None:
        return None, (jsonify({
            "error": "Camera runtime is not started",
            "camera_id": camera_id,
        }), 503)
    return worker, None


@cameras_blueprint.get("/api/cameras/<path:camera_id>/snapshot")
def camera_snapshot(camera_id: str):
    worker, error = _stream_worker(camera_id)
    if error is not None:
        return error

    latest = worker.get_latest_frame(copy=True)
    if latest is None:
        return jsonify({
            "error": "No camera frame is available yet",
            "camera_id": camera_id,
        }), 503

    try:
        jpeg = encode_jpeg(_render(camera_id, latest.frame), quality=85)
    except ValueError as encode_error:
        return jsonify({"error": str(encode_error), "camera_id": camera_id}), 500

    response = Response(jpeg, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Camera-Sequence"] = str(latest.sequence)
    response.headers["X-Captured-At"] = str(latest.captured_at)
    return response


@cameras_blueprint.get("/api/cameras/<path:camera_id>/stream")
def camera_stream(camera_id: str):
    worker, error = _stream_worker(camera_id)
    if error is not None:
        return error
    if worker.get_latest_frame(copy=False) is None:
        return jsonify({
            "error": "No camera frame is available yet",
            "camera_id": camera_id,
        }), 503

    detection_manager = current_app.extensions["detection_manager"]
    response = Response(
        mjpeg_generator(
            worker,
            frame_transform=lambda frame: detection_manager.render(camera_id, frame),
        ),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@cameras_blueprint.get("/api/cameras/<path:camera_id>/raw-stream")
def camera_raw_stream(camera_id: str):
    """Stream the shared camera frames without AI annotations.

    The restricted-zone editor uses this endpoint so the operator can draw on
    a moving image without opening another connection to the physical camera.
    """
    worker, error = _stream_worker(camera_id)
    if error is not None:
        return error
    if worker.get_latest_frame(copy=False) is None:
        return jsonify({
            "error": "No camera frame is available yet",
            "camera_id": camera_id,
        }), 503

    response = Response(
        mjpeg_generator(worker),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response
