"""Small, failure-isolated ONVIF controller for the CAM 01 Tapo camera."""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import unquote, urlsplit


class PtzError(RuntimeError):
    """A safe operator-facing PTZ error."""


class TapoPtzController:
    CAMERA_ID = "CAM 01"
    PRESET_NAMES = {"P1": "SentryLab P1", "P2": "SentryLab P2", "P3": "SentryLab P3"}

    def __init__(
        self,
        rtsp_url_env: str = "SENTRYLAB_CAM01_RTSP_URL",
        onvif_port: int = 2020,
        speed: float = 0.35,
        move_seconds: float = 0.35,
        camera_factory=None,
        sleep=time.sleep,
    ) -> None:
        self.rtsp_url_env = rtsp_url_env
        self.onvif_port = int(onvif_port)
        self.speed = max(0.05, min(1.0, float(speed)))
        self.move_seconds = max(0.08, min(2.0, float(move_seconds)))
        self._camera_factory = camera_factory
        self._sleep = sleep
        self._lock = threading.RLock()
        self._service = None
        self._profile_token = None
        self._credential_values: tuple[str, ...] = ()
        self.last_error: str | None = None

    def capabilities(self, camera_id: str) -> dict:
        is_tapo = camera_id == self.CAMERA_ID
        return {
            "camera_id": camera_id,
            "digital_zoom": camera_id in {"CAM 01", "CAM 02"},
            "min_zoom": 1.0,
            "max_zoom": 3.0,
            "zoom_step": 0.25,
            "pan_tilt": is_tapo,
            "presets": is_tapo,
            "preset_slots": list(self.PRESET_NAMES) if is_tapo else [],
            "configured": bool(os.getenv(self.rtsp_url_env)) if is_tapo else True,
            "last_error": self.last_error if is_tapo else None,
        }

    def _credentials(self) -> tuple[str, str, str]:
        value = os.getenv(self.rtsp_url_env, "")
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "rtsp" or not parsed.hostname or parsed.username is None:
            raise PtzError("CAM 01 Tapo Camera Account is not configured. Start with scripts/setup_tapo.ps1.")
        username = unquote(parsed.username)
        password = unquote(parsed.password or "")
        if not username or not password:
            raise PtzError("CAM 01 Tapo Camera Account username or password is missing.")
        self._credential_values = (username, password, value)
        return parsed.hostname, username, password

    def _safe_error(self, error: Exception) -> PtzError:
        message = str(error) or error.__class__.__name__
        for secret in self._credential_values:
            if secret:
                message = message.replace(secret, "***")
        self.last_error = message
        return PtzError(message)

    def _connect(self):
        if self._service is not None and self._profile_token is not None:
            return self._service, self._profile_token
        host, username, password = self._credentials()
        try:
            factory = self._camera_factory
            if factory is None:
                from onvif import ONVIFCamera
                factory = ONVIFCamera
            camera = factory(host, self.onvif_port, username, password)
            media = camera.create_media_service()
            profiles = media.GetProfiles()
            if not profiles:
                raise RuntimeError("The Tapo returned no ONVIF media profile.")
            self._service = camera.create_ptz_service()
            self._profile_token = profiles[0].token
            self.last_error = None
            return self._service, self._profile_token
        except PtzError:
            raise
        except Exception as error:
            raise self._safe_error(error) from error

    def _request(self, name: str):
        service, token = self._connect()
        request = service.create_type(name)
        request.ProfileToken = token
        return service, request

    def move(self, direction: str) -> dict:
        vectors = {
            "left": (-self.speed, 0.0), "right": (self.speed, 0.0),
            "up": (0.0, self.speed), "down": (0.0, -self.speed),
        }
        direction = str(direction).lower()
        if direction not in vectors:
            raise PtzError("direction must be up, down, left, or right")
        with self._lock:
            try:
                service, request = self._request("ContinuousMove")
                pan, tilt = vectors[direction]
                request.Velocity = {"PanTilt": {"x": pan, "y": tilt}}
                service.ContinuousMove(request)
                self._sleep(self.move_seconds)
                self._stop_locked(service)
                self.last_error = None
                return {"ok": True, "direction": direction}
            except PtzError:
                raise
            except Exception as error:
                self._service = None
                raise self._safe_error(error) from error

    def _stop_locked(self, service=None) -> None:
        if service is None:
            service, _ = self._connect()
        request = service.create_type("Stop")
        request.ProfileToken = self._profile_token
        request.PanTilt = True
        request.Zoom = False
        service.Stop(request)

    def home(self) -> dict:
        with self._lock:
            try:
                service, request = self._request("GotoHomePosition")
                service.GotoHomePosition(request)
                self.last_error = None
                return {"ok": True, "position": "HOME"}
            except Exception as error:
                self._service = None
                raise self._safe_error(error) from error

    @staticmethod
    def _value(item, name: str):
        return item.get(name) if isinstance(item, dict) else getattr(item, name, None)

    def _presets(self, service) -> list:
        request = service.create_type("GetPresets")
        request.ProfileToken = self._profile_token
        return list(service.GetPresets(request) or [])

    def preset_status(self) -> dict:
        with self._lock:
            try:
                service, _ = self._connect()
                names = {self._value(item, "Name") for item in self._presets(service)}
                return {slot: name in names for slot, name in self.PRESET_NAMES.items()}
            except Exception as error:
                if isinstance(error, PtzError):
                    raise
                raise self._safe_error(error) from error

    def save_preset(self, slot: str) -> dict:
        slot = str(slot).upper()
        if slot not in self.PRESET_NAMES:
            raise PtzError("preset must be P1, P2, or P3")
        with self._lock:
            try:
                service, request = self._request("SetPreset")
                request.PresetName = self.PRESET_NAMES[slot]
                existing = next((item for item in self._presets(service)
                                 if self._value(item, "Name") == request.PresetName), None)
                if existing is not None:
                    request.PresetToken = self._value(existing, "token") or self._value(existing, "PresetToken")
                service.SetPreset(request)
                self.last_error = None
                return {"ok": True, "preset": slot, "saved": True}
            except Exception as error:
                self._service = None
                raise self._safe_error(error) from error

    def goto_preset(self, slot: str) -> dict:
        slot = str(slot).upper()
        if slot not in self.PRESET_NAMES:
            raise PtzError("preset must be P1, P2, or P3")
        with self._lock:
            try:
                service, request = self._request("GotoPreset")
                preset = next((item for item in self._presets(service)
                               if self._value(item, "Name") == self.PRESET_NAMES[slot]), None)
                if preset is None:
                    raise PtzError(f"{slot} is empty. Choose Save Current, then {slot}.")
                request.PresetToken = self._value(preset, "token") or self._value(preset, "PresetToken")
                service.GotoPreset(request)
                self.last_error = None
                return {"ok": True, "preset": slot}
            except PtzError:
                raise
            except Exception as error:
                self._service = None
                raise self._safe_error(error) from error
