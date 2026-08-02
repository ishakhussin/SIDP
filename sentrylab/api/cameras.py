"""Read-only camera status endpoints."""

from flask import Blueprint, Response, current_app, jsonify, request

from sentrylab.streaming import encode_jpeg, mjpeg_generator


cameras_blueprint = Blueprint("cameras", __name__)


def _manager():
    return current_app.extensions["camera_manager"]


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
