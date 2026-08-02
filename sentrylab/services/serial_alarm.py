"""Laptop-to-ESP32 alarm transport over a reconnecting USB serial link."""

from __future__ import annotations

import logging
import threading
import time

from sentrylab.services.detection_manager import SUPPORTED_DETECTORS


LOGGER = logging.getLogger(__name__)


def _default_serial_factory(port: str, baud_rate: int):
    import serial

    return serial.Serial(
        port=port,
        baudrate=baud_rate,
        timeout=0.15,
        write_timeout=0.5,
    )


def _discover_esp32_port() -> str | None:
    """Return one connected CH340 ESP32 port without selecting unrelated devices."""
    from serial.tools import list_ports

    matches = []
    for port in list_ports.comports():
        description = str(port.description or "").upper()
        is_ch340 = (
            (port.vid == 0x1A86 and port.pid == 0x7523)
            or "CH340" in description
            or "CH341" in description
        )
        if is_ch340:
            matches.append(port.device)
    matches = sorted(set(matches))
    if len(matches) > 1:
        raise RuntimeError(
            "Multiple CH340 serial devices detected; set SENTRYLAB_ALARM_COM_PORT"
        )
    return matches[0] if matches else None


class SerialAlarmService:
    """Keeps the alarm on while any confirmed detector level is UNSAFE."""

    def __init__(
        self,
        camera_manager,
        detection_manager,
        port: str | None,
        baud_rate: int = 115200,
        poll_interval_seconds: float = 1.0,
        clear_delay_seconds: float = 2.0,
        serial_factory=_default_serial_factory,
        port_discovery=_discover_esp32_port,
        clock=time.monotonic,
    ) -> None:
        self.camera_manager = camera_manager
        self.detection_manager = detection_manager
        self.requested_port = str(port).strip() if port else None
        self.port = self.requested_port
        self.auto_detect = self.requested_port is None
        self.baud_rate = int(baud_rate)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.clear_delay_seconds = float(clear_delay_seconds)
        self.serial_factory = serial_factory
        self.port_discovery = port_discovery
        self.clock = clock
        self._serial = None
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._alarm_required = False
        self._alarm_active = False
        self._unsafe_sources = []
        self._held_sources = []
        self._clear_started_at = None
        self._last_command = None
        self._last_command_at = None
        self._last_error = None
        self._reconnect_count = 0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="sentrylab-serial-alarm",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            self._thread = None
        if self._serial is not None:
            try:
                self._write_line("ALARM_OFF")
            except Exception:
                LOGGER.debug("Could not send final ALARM_OFF", exc_info=True)
        self._close_serial()
        with self._lock:
            self._alarm_required = False
            self._alarm_active = False
            self._unsafe_sources = []
            self._held_sources = []
            self._clear_started_at = None

    def _confirmed_unsafe_sources(self) -> list[str]:
        sources = []
        for camera in self.camera_manager.statuses():
            camera_id = camera["camera_id"]
            for detector in SUPPORTED_DETECTORS:
                status = self.detection_manager.status(camera_id, detector)
                if not status.get("enabled"):
                    continue
                if "UNSAFE" in set(status.get("levels", {}).values()):
                    sources.append(f"{camera_id}:{detector}")
        return sorted(sources)

    def poll_once(self, now: float | None = None) -> str:
        """Evaluate current safety state and send one heartbeat command."""
        current = self.clock() if now is None else float(now)
        sources = self._confirmed_unsafe_sources()
        with self._lock:
            self._unsafe_sources = sources
            if sources:
                self._held_sources = list(sources)
                self._clear_started_at = None
                required = True
            elif self._alarm_required:
                if self._clear_started_at is None:
                    self._clear_started_at = current
                required = current - self._clear_started_at < self.clear_delay_seconds
            else:
                required = False
            self._alarm_required = required
            held_sources = list(self._held_sources)

        if required:
            command = "ALARM_ON:" + ",".join(sources or held_sources)
        elif self._alarm_active:
            command = "ALARM_OFF"
        else:
            command = "HEARTBEAT"
        self._send(command, current)
        return command

    def _connect(self) -> None:
        if self._serial is not None:
            return
        try:
            if self.port is None:
                self.port = self.port_discovery()
            if self.port is None:
                raise RuntimeError("No connected CH340 ESP32 alarm device detected")
            connection = self.serial_factory(self.port, self.baud_rate)
            with self._lock:
                self._serial = connection
                self._last_error = None
        except Exception as error:
            with self._lock:
                self._last_error = str(error)
                self._reconnect_count += 1
            raise

    def _write_line(self, command: str) -> None:
        self._serial.write((command + "\n").encode("ascii"))
        self._serial.flush()

    def _send(self, command: str, now: float) -> None:
        try:
            self._connect()
            self._write_line(command)
        except Exception as error:
            with self._lock:
                self._last_error = str(error)
                self._alarm_active = False
            self._close_serial()
            if self.auto_detect:
                with self._lock:
                    self.port = None
            return
        with self._lock:
            self._last_command = command
            self._last_command_at = now
            self._last_error = None
            if command.startswith("ALARM_ON:"):
                self._alarm_active = True
            elif command == "ALARM_OFF":
                self._alarm_active = False
                self._held_sources = []

    def _close_serial(self) -> None:
        with self._lock:
            connection = self._serial
            self._serial = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                LOGGER.debug("Serial close failed", exc_info=True)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                LOGGER.exception("Serial alarm polling failed")
            self._stop_event.wait(self.poll_interval_seconds)

    def status(self) -> dict:
        with self._lock:
            return {
                "configured": bool(self.requested_port) or self.auto_detect,
                "connected": self._serial is not None,
                "port": self.port,
                "auto_detect": self.auto_detect,
                "baud_rate": self.baud_rate,
                "running": self._thread is not None and self._thread.is_alive(),
                "alarm_required": self._alarm_required,
                "alarm_active": self._alarm_active,
                "unsafe_sources": list(self._unsafe_sources),
                "last_command": self._last_command,
                "last_command_at": self._last_command_at,
                "last_error": self._last_error,
                "reconnect_count": self._reconnect_count,
            }
