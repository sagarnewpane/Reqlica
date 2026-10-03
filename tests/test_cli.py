"""Check CLI startup, shutdown, and child-process failure handling."""

import subprocess
import sys
import unittest
from unittest.mock import MagicMock, call, patch

from reqlica.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.console = MagicMock()
        self.mock_server = MagicMock()
        self.console.poll.return_value = None
        self.mock_server.poll.return_value = None
        self.arguments = patch.object(sys, "argv", ["reqlica", "start"])
        self.signals = patch("reqlica.cli.signal.signal")
        self.launcher = patch(
            "reqlica.cli.subprocess.Popen",
            side_effect=[self.console, self.mock_server],
        )
        self.arguments.start()
        self.signals.start()
        self.launch = self.launcher.start()
        self.addCleanup(self.arguments.stop)
        self.addCleanup(self.signals.stop)
        self.addCleanup(self.launcher.stop)

    def test_start_and_interrupt_stop_both_servers(self):
        with patch("reqlica.cli.time.sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(main(), 0)

        self.assertEqual(
            self.launch.call_args_list,
            [
                call(
                    [sys.executable, "-m", "reqlica.console.app"],
                    start_new_session=True,
                ),
                call(
                    [sys.executable, "-m", "reqlica.runtime.app"],
                    start_new_session=True,
                ),
            ],
        )
        for process in (self.console, self.mock_server):
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=5)

    def test_child_exit_stops_other_server(self):
        self.console.poll.return_value = 1

        self.assertEqual(main(), 1)

        self.console.terminate.assert_not_called()
        self.mock_server.terminate.assert_called_once_with()
        self.mock_server.wait.assert_called_once_with(timeout=5)

    def test_partial_startup_failure_stops_started_server(self):
        self.launch.side_effect = [self.console, OSError("Cannot start process")]

        self.assertEqual(main(), 1)

        self.console.terminate.assert_called_once_with()
        self.console.wait.assert_called_once_with(timeout=5)

    def test_shutdown_kills_unresponsive_server(self):
        self.console.wait.side_effect = [
            subprocess.TimeoutExpired("console", 5),
            0,
        ]
        with patch("reqlica.cli.time.sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(main(), 0)

        self.console.kill.assert_called_once_with()
        self.assertEqual(self.console.wait.call_args_list, [call(timeout=5), call()])
        self.mock_server.wait.assert_called_once_with(timeout=5)

    def test_missing_command_does_not_start_servers(self):
        with patch.object(sys, "argv", ["reqlica"]):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 2)
        self.launch.assert_not_called()
