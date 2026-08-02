import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sentrylab.services.ptz import PtzError, TapoPtzController


class FakePtzService:
    def __init__(self):
        self.calls = []
        self.presets = []

    def create_type(self, name):
        return SimpleNamespace(_type=name)

    def ContinuousMove(self, request):
        self.calls.append(("move", request.Velocity))

    def Stop(self, request):
        self.calls.append(("stop", request.PanTilt))

    def GotoHomePosition(self, request):
        self.calls.append(("home", request.ProfileToken))

    def GetPresets(self, request):
        return self.presets

    def SetPreset(self, request):
        token = getattr(request, "PresetToken", f"token-{len(self.presets) + 1}")
        self.presets = [item for item in self.presets if item.Name != request.PresetName]
        self.presets.append(SimpleNamespace(Name=request.PresetName, token=token))
        self.calls.append(("save", request.PresetName))

    def GotoPreset(self, request):
        self.calls.append(("goto", request.PresetToken))


class FakeCamera:
    def __init__(self, service):
        self.service = service

    def create_media_service(self):
        return SimpleNamespace(GetProfiles=lambda: [SimpleNamespace(token="profile-1")])

    def create_ptz_service(self):
        return self.service


class PtzControllerTest(unittest.TestCase):
    def setUp(self):
        self.service = FakePtzService()
        self.factory_args = None

        def factory(*args):
            self.factory_args = args
            return FakeCamera(self.service)

        self.controller = TapoPtzController(camera_factory=factory, sleep=lambda _seconds: None)
        self.environment = patch.dict(
            os.environ,
            {"SENTRYLAB_CAM01_RTSP_URL": "rtsp://lab%20user:p%40ss@192.168.1.20:554/stream1"},
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_cam02_has_zoom_but_no_physical_ptz(self):
        capability = self.controller.capabilities("CAM 02")
        self.assertTrue(capability["digital_zoom"])
        self.assertFalse(capability["pan_tilt"])
        self.assertFalse(capability["presets"])

    def test_move_uses_decoded_camera_account_and_stops(self):
        result = self.controller.move("left")
        self.assertTrue(result["ok"])
        self.assertEqual(self.factory_args, ("192.168.1.20", 2020, "lab user", "p@ss"))
        self.assertEqual(self.service.calls[0][0], "move")
        self.assertEqual(self.service.calls[1], ("stop", True))

    def test_save_and_open_named_preset(self):
        self.controller.save_preset("P1")
        self.assertEqual(self.controller.preset_status()["P1"], True)
        result = self.controller.goto_preset("P1")
        self.assertEqual(result["preset"], "P1")
        self.assertEqual(self.service.calls[-1][0], "goto")

    def test_empty_preset_has_clear_error(self):
        with self.assertRaisesRegex(PtzError, "P2 is empty"):
            self.controller.goto_preset("P2")

    def test_patrol_requires_two_saved_presets(self):
        self.controller.save_preset("P1")
        with self.assertRaisesRegex(PtzError, "at least two saved presets"):
            self.controller.start_patrol()


class PtzApiTest(unittest.TestCase):
    def test_cam02_controls_report_zoom_only(self):
        from sentrylab import create_app

        response = create_app().test_client().get("/api/cameras/CAM%2002/controls")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["digital_zoom"])
        self.assertFalse(response.get_json()["pan_tilt"])

    def test_cam01_move_routes_to_controller(self):
        from sentrylab import create_app

        app = create_app()
        controller = Mock()
        controller.move.return_value = {"ok": True, "direction": "left"}
        app.extensions["ptz_controller"] = controller
        response = app.test_client().post("/api/cameras/CAM%2001/ptz", json={"action": "left"})
        self.assertEqual(response.status_code, 200)
        controller.move.assert_called_once_with("left")

    def test_cam01_patrol_routes_to_controller(self):
        from sentrylab import create_app

        app = create_app()
        controller = Mock()
        controller.start_patrol.return_value = {"patrol_active": True}
        app.extensions["ptz_controller"] = controller
        response = app.test_client().post(
            "/api/cameras/CAM%2001/patrol", json={"enabled": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["patrol_active"])
        controller.start_patrol.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
