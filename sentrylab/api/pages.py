"""Local dashboard pages."""

from flask import Blueprint, render_template


pages_blueprint = Blueprint("pages", __name__)


@pages_blueprint.get("/")
@pages_blueprint.get("/sentrylab-dashboard.html")
def dashboard():
    return render_template("index.html")


@pages_blueprint.get("/event.html")
def event_log():
    return render_template("event.html")


@pages_blueprint.get("/overview.html")
@pages_blueprint.get("/sentrylab-gallery.html")
def overview():
    return render_template("overview.html")
