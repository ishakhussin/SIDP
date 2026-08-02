"""AI model installation status endpoint."""

from flask import Blueprint, current_app, jsonify


models_blueprint = Blueprint("models", __name__)


@models_blueprint.get("/api/models/status")
def model_status():
    return jsonify(current_app.extensions["model_inventory"].status())
