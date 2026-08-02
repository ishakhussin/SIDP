import unittest

from sentrylab import create_app


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()

    def test_dashboard_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SentryLab", response.data)
        self.assertIn(b"/static/dashboard.js", response.data)

    def test_dashboard_alias_loads(self):
        response = self.client.get("/sentrylab-dashboard.html")
        self.assertEqual(response.status_code, 200)

    def test_event_log_page_loads(self):
        response = self.client.get("/event.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Event Log", response.data)
        self.assertIn(b"/static/event.js", response.data)

    def test_overview_pages_load(self):
        for path in ("/overview.html", "/sentrylab-gallery.html"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"System Overview", response.data)
            self.assertIn(b"/static/overview.js", response.data)

    def test_secondary_page_scripts_use_v123_apis(self):
        event_script = self.client.get("/static/event.js")
        overview_script = self.client.get("/static/overview.js")
        try:
            self.assertEqual(event_script.status_code, 200)
            self.assertEqual(overview_script.status_code, 200)
            self.assertIn(b"/api/incidents", event_script.data)
            self.assertIn(b"/api/log-entries", event_script.data)
            self.assertIn(b"export.zip", event_script.data)
            self.assertIn(b"/api/cameras", overview_script.data)
            self.assertIn(b"/api/dashboard/summary", overview_script.data)
            self.assertNotIn(b"/api/events", event_script.data)
        finally:
            event_script.close()
            overview_script.close()

    def test_dashboard_script_uses_v123_camera_api(self):
        response = self.client.get("/static/dashboard.js")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"/api/cameras", response.data)
            self.assertIn(b"/detectors/restricted-zone", response.data)
            self.assertIn(b"/detectors/unsafe-proximity", response.data)
            self.assertIn(b"/detectors/ppe", response.data)
            self.assertIn(b"/api/models/status", response.data)
            self.assertIn(b"/api/alarm/status", response.data)
            self.assertIn(b"/power", response.data)
            self.assertIn(b"localStorage", response.data)
            self.assertIn(b"recent-events-refresh", response.data)
            self.assertIn(b"all detector services are paused", response.data)
            self.assertIn(b"MODEL MISSING", response.data)
            self.assertIn(b"capture_fps", response.data)
            self.assertIn(b"/restricted-zone?preset=HOME", response.data)
            self.assertIn(b"/raw-stream", response.data)
            self.assertIn(b"/event.html?incident_id=", response.data)
            self.assertIn(b"incident_id=", response.data)
            self.assertIn(b"Open 10s Clip", response.data)
            self.assertNotIn(b"/api/camera/pose", response.data)
            self.assertNotIn(b"/api/camera/tapo", response.data)
        finally:
            response.close()

    def test_recent_events_has_refresh_button(self):
        response = self.client.get("/")
        self.assertIn(b'id="recent-events-refresh"', response.data)
        self.assertIn(b'Refresh recent events', response.data)

    def test_camera_control_ui_is_connected(self):
        page = self.client.get("/sentrylab-dashboard.html")
        script = self.client.get("/static/dashboard.js")
        try:
            self.assertIn(b"save-preset-mode", page.data)
            self.assertIn(b"/controls", script.data)
            self.assertIn(b"/ptz", script.data)
            self.assertIn(b"ZOOM_LEVELS_KEY", script.data)
        finally:
            page.close()
            script.close()


if __name__ == "__main__":
    unittest.main()
