import tempfile
import unittest
from pathlib import Path

from sentrylab import create_app
from sentrylab.model_inventory import MODEL_GROUPS, ModelInventory


class ModelInventoryTest(unittest.TestCase):
    def test_empty_model_directory_reports_every_file_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            status = ModelInventory(Path(directory)).status()

        expected = sum(len(group.files) for group in MODEL_GROUPS)
        self.assertFalse(status["ready"])
        self.assertEqual(status["missing_count"], expected)
        self.assertTrue(all(not group["ready"] for group in status["groups"]))

    def test_complete_nonempty_bundle_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for group in MODEL_GROUPS:
                for relative_path in group.files:
                    path = root / relative_path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"model")
            status = ModelInventory(root).status()

        self.assertTrue(status["ready"])
        self.assertEqual(status["missing_count"], 0)
        self.assertTrue(all(group["ready"] for group in status["groups"]))

    def test_zero_byte_model_is_treated_as_incomplete_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_file = MODEL_GROUPS[0].files[0]
            path = root / first_file
            path.parent.mkdir(parents=True)
            path.touch()
            status = ModelInventory(root).status()

        restricted = status["groups"][0]
        self.assertFalse(restricted["ready"])
        self.assertIn(first_file, restricted["missing"])

    def test_model_status_api_does_not_load_ai_frameworks(self):
        response = create_app().test_client().get("/api/models/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("ready", payload)
        self.assertEqual(len(payload["groups"]), 3)


if __name__ == "__main__":
    unittest.main()
