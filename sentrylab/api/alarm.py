"""Read-only ESP32 alarm status endpoint."""

from flask import Blueprint, current_app, jsonify


alarm_blueprint = Blueprint("alarm", __name__)


@alarm_blueprint.get("/api/alarm/status")
def alarm_status():
    return jsonify(current_app.extensions["serial_alarm"].status())
