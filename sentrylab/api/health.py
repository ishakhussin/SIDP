"""Small endpoint used to verify the web application independently of AI."""

from flask import Blueprint, jsonify


health_blueprint = Blueprint("health", __name__)


@health_blueprint.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "sentrylab", "version": "v123"})
