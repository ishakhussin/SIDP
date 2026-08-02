import unittest

from sentrylab import create_app


class HealthApiTest(unittest.TestCase):
    def test_health_endpoint(self):
        client = create_app().test_client()
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ok"], True)


if __name__ == "__main__":
    unittest.main()
