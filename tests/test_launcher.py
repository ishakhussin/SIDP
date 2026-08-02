import unittest
from unittest.mock import patch

import app as launcher


class LauncherTest(unittest.TestCase):
    def test_runtime_stops_when_server_returns(self):
        with (
            patch.object(launcher.runtime, "start") as start,
            patch.object(launcher.runtime, "stop") as stop,
            patch.object(launcher.app, "run") as run,
        ):
            launcher.main()
        start.assert_called_once_with(launcher.app)
        run.assert_called_once()
        stop.assert_called_once_with()

    def test_runtime_stops_when_server_raises(self):
        with (
            patch.object(launcher.runtime, "start"),
            patch.object(launcher.runtime, "stop") as stop,
            patch.object(launcher.app, "run", side_effect=RuntimeError("server failed")),
        ):
            with self.assertRaises(RuntimeError):
                launcher.main()
        stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
