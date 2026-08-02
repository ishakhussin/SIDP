"""Flask application factory for SentryLab."""

from flask import Flask

from sentrylab.api.cameras import cameras_blueprint
from sentrylab.api.alarm import alarm_blueprint
from sentrylab.api.health import health_blueprint
from sentrylab.api.incidents import incidents_blueprint
from sentrylab.api.models import models_blueprint
from sentrylab.api.pages import pages_blueprint
from sentrylab.api.ppe import ppe_blueprint
from sentrylab.api.restricted_zone import restricted_zone_blueprint
from sentrylab.api.unsafe_proximity import unsafe_proximity_blueprint
from sentrylab.cameras import CameraManager
from sentrylab.config import Settings
from sentrylab.database import (
    Database,
    DetectorSettingsRepository,
    IncidentRepository,
    MonitoringLogRepository,
    ZoneRepository,
)
from sentrylab.services.detection_manager import DetectionManager
from sentrylab.services.monitoring_heartbeat import MonitoringHeartbeatService
from sentrylab.services.serial_alarm import SerialAlarmService
from sentrylab.services.ptz import TapoPtzController
from sentrylab.model_inventory import ModelInventory


def create_app(settings: Settings | None = None) -> Flask:
    selected = settings or Settings.from_environment()
    app = Flask(
        __name__,
        template_folder=str(selected.template_dir),
        static_folder=str(selected.static_dir),
    )
    app.config["SENTRYLAB_SETTINGS"] = selected
    database = Database(selected.database_path)
    database.initialize()
    app.extensions["database"] = database
    app.extensions["incident_repository"] = IncidentRepository(database)
    app.extensions["monitoring_log_repository"] = MonitoringLogRepository(database)
    app.extensions["zone_repository"] = ZoneRepository(database)
    app.extensions["detector_settings_repository"] = DetectorSettingsRepository(database)
    app.extensions["camera_manager"] = CameraManager.from_file(
        selected.config_dir / "cameras.json"
    )
    app.extensions["ptz_controller"] = TapoPtzController()
    app.extensions["model_inventory"] = ModelInventory(selected.model_dir)
    app.extensions["detection_manager"] = DetectionManager(
        camera_manager=app.extensions["camera_manager"],
        zone_repository=app.extensions["zone_repository"],
        settings_repository=app.extensions["detector_settings_repository"],
        incident_repository=app.extensions["incident_repository"],
        model_dir=selected.model_dir,
        clips_dir=selected.clips_dir,
    )
    app.extensions["monitoring_heartbeat"] = MonitoringHeartbeatService(
        camera_manager=app.extensions["camera_manager"],
        detection_manager=app.extensions["detection_manager"],
        repository=app.extensions["monitoring_log_repository"],
    )
    app.extensions["serial_alarm"] = SerialAlarmService(
        camera_manager=app.extensions["camera_manager"],
        detection_manager=app.extensions["detection_manager"],
        port=selected.alarm_serial_port,
        baud_rate=selected.alarm_baud_rate,
    )
    app.register_blueprint(alarm_blueprint)
    app.register_blueprint(health_blueprint)
    app.register_blueprint(cameras_blueprint)
    app.register_blueprint(incidents_blueprint)
    app.register_blueprint(models_blueprint)
    app.register_blueprint(pages_blueprint)
    app.register_blueprint(ppe_blueprint)
    app.register_blueprint(restricted_zone_blueprint)
    app.register_blueprint(unsafe_proximity_blueprint)
    return app
