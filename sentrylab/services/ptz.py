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
        patrol_dwell_seconds: float = 10.0,
        patrol_settle_seconds: float = 2.0,
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
        self.patrol_dwell_seconds = max(5.0, float(patrol_dwell_seconds))
        self.patrol_settle_seconds = max(0.0, float(patrol_settle_seconds))
        self._patrol_lock = threading.RLock()
        self._patrol_stop = threading.Event()
        self._patrol_thread = None
        self._patrol_active = False
        self._moving = False
        self._current_preset = "HOME"

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
            "auto_patrol": is_tapo,
            "preset_slots": list(self.PRESET_NAMES) if is_tapo else [],
            "configured": bool(os.getenv(self.rtsp_url_env)) if is_tapo else True,
            "last_error": self.last_error if is_tapo else None,
            "patrol_active": self.patrol_active if is_tapo else False,
            "moving": self.moving if is_tapo else False,
            "current_preset": self.current_preset if is_tapo else "HOME",
            "patrol_dwell_seconds": self.patrol_dwell_seconds,
            "patrol_settle_seconds": self.patrol_settle_seconds,
        }

    @property
    def patrol_active(self) -> bool:
        with self._patrol_lock:
            return self._patrol_active

    @property
    def moving(self) -> bool:
        with self._patrol_lock:
            return self._moving

    @property
    def current_preset(self) -> str:
        with self._patrol_lock:
            return self._current_preset

    def monitoring_ready(self, camera_id: str) -> bool:
        return camera_id != self.CAMERA_ID or not self.moving

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
        self.stop_patrol()
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
        self.stop_patrol()
        with self._patrol_lock:
            self._moving = True
        with self._lock:
            try:
                service, request = self._request("GotoHomePosition")
                service.GotoHomePosition(request)
                self._sleep(self.patrol_settle_seconds)
                with self._patrol_lock:
                    self._current_preset = "HOME"
                self.last_error = None
                return {"ok": True, "position": "HOME"}
            except Exception as error:
                self._service = None
                raise self._safe_error(error) from error
            finally:
                with self._patrol_lock:
                    self._moving = False

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
        self.stop_patrol()
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
        self.stop_patrol()
        self._patrol_stop.clear()
        return self._goto_preset(slot, cancelable=False)

    def _goto_preset(self, slot: str, cancelable: bool = True) -> dict:
        slot = str(slot).upper()
        if slot not in self.PRESET_NAMES:
            raise PtzError("preset must be P1, P2, or P3")
        with self._patrol_lock:
            self._moving = True
        with self._lock:
            try:
                service, request = self._request("GotoPreset")
                preset = next((item for item in self._presets(service)
                               if self._value(item, "Name") == self.PRESET_NAMES[slot]), None)
                if preset is None:
                    raise PtzError(f"{slot} is empty. Choose Save Current, then {slot}.")
                request.PresetToken = self._value(preset, "token") or self._value(preset, "PresetToken")
                service.GotoPreset(request)
                cancelled = self._patrol_stop.wait(self.patrol_settle_seconds) if cancelable else False
                if not cancelable:
                    self._sleep(self.patrol_settle_seconds)
                if cancelled:
                    return {"ok": True, "preset": slot, "cancelled": True}
                with self._patrol_lock:
                    self._current_preset = slot
                self.last_error = None
                return {"ok": True, "preset": slot}
            except PtzError:
                raise
            except Exception as error:
                self._service = None
                raise self._safe_error(error) from error
            finally:
                with self._patrol_lock:
                    self._moving = False

    def start_patrol(self) -> dict:
        saved = self.preset_status()
        slots = [slot for slot in self.PRESET_NAMES if saved.get(slot)]
        if len(slots) < 2:
            raise PtzError("Auto Patrol needs at least two saved presets (P1, P2 or P3).")
        with self._patrol_lock:
            if self._patrol_thread is not None and self._patrol_thread.is_alive():
                return self.capabilities(self.CAMERA_ID)
            self._patrol_stop.clear()
            self._patrol_active = True
            self._patrol_thread = threading.Thread(
                target=self._patrol_loop,
                args=(slots,),
                daemon=True,
                name="tapo-auto-patrol",
            )
            self._patrol_thread.start()
        return self.capabilities(self.CAMERA_ID)

    def _patrol_loop(self, slots: list[str]) -> None:
        try:
            while not self._patrol_stop.is_set():
                for slot in slots:
                    if self._patrol_stop.is_set():
                        break
                    self._goto_preset(slot)
                    if self._patrol_stop.wait(self.patrol_dwell_seconds):
                        break
        except Exception as error:
            self.last_error = str(error)
        finally:
            with self._patrol_lock:
                self._patrol_active = False
                self._moving = False

    def stop_patrol(self) -> dict:
        self._patrol_stop.set()
        with self._patrol_lock:
            thread = self._patrol_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(3.0)
        with self._patrol_lock:
            self._patrol_active = False
            self._moving = False
        return self.capabilities(self.CAMERA_ID)
