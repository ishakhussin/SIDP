"""Application lifecycle boundary.

Camera and detector managers will be registered here incrementally. Importing
the Flask application never opens a camera or loads an AI model.
"""

from __future__ import annotations

import logging
import threading

from flask import Flask


LOGGER = logging.getLogger(__name__)


class Runtime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._camera_manager = None
        self._detection_manager = None
        self._monitoring_heartbeat = None
        self._serial_alarm = None

    @property
    def started(self) -> bool:
        return self._started

    def start(self, app: Flask) -> None:
        with self._lock:
            if self._started:
                return

            settings = app.config["SENTRYLAB_SETTINGS"]
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            settings.clips_dir.mkdir(parents=True, exist_ok=True)
            self._camera_manager = app.extensions["camera_manager"]
            self._camera_manager.start_enabled()
            self._detection_manager = app.extensions["detection_manager"]
            self._detection_manager.start_enabled()
            self._monitoring_heartbeat = app.extensions["monitoring_heartbeat"]
            self._monitoring_heartbeat.start()
            self._serial_alarm = app.extensions["serial_alarm"]
            self._serial_alarm.start()
            self._started = True
            LOGGER.info("SentryLab runtime started")

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            manager = self._camera_manager
            detection_manager = self._detection_manager
            monitoring_heartbeat = self._monitoring_heartbeat
            serial_alarm = self._serial_alarm
            self._camera_manager = None
            self._detection_manager = None
            self._monitoring_heartbeat = None
            self._serial_alarm = None
        if serial_alarm is not None:
            serial_alarm.stop()
        if monitoring_heartbeat is not None:
            monitoring_heartbeat.stop()
        if detection_manager is not None:
            detection_manager.stop_all()
        if manager is not None:
            manager.stop_all()
        LOGGER.info("SentryLab runtime stopped")


runtime = Runtime()
