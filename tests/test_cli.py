"""Check CLI startup, shutdown, and child-process failure handling."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from reqlica.cli import main
from reqlica.projects.store import get_project


class CliTests(unittest.TestCase):
    def setUp(self):
        self.console = MagicMock()
        self.mock_server = MagicMock()
        self.console.poll.return_value = None
        self.mock_server.poll.return_value = None
        self.arguments = patch.object(sys, "argv", ["reqlica", "start"])
        self.signals = patch("reqlica.cli.signal.signal")
        self.groups = patch("reqlica.cli.os.killpg", create=True)
        self.launcher = patch(
            "reqlica.cli.subprocess.Popen",
            side_effect=[self.console, self.mock_server],
        )
        self.arguments.start()
        self.signals.start()
        self.kill_group = self.groups.start()
        self.launch = self.launcher.start()
        self.addCleanup(self.arguments.stop)
        self.addCleanup(self.signals.stop)
        self.addCleanup(self.groups.stop)
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
        if os.name == "posix":
            self.assertEqual(
                self.kill_group.call_args_list,
                [
                    call(self.console.pid, signal.SIGKILL),
                    call(self.mock_server.pid, signal.SIGKILL),
                ],
            )

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
        if os.name == "posix":
            self.kill_group.assert_any_call(self.console.pid, signal.SIGKILL)
        self.assertEqual(self.console.wait.call_args_list, [call(timeout=5), call()])
        self.mock_server.wait.assert_called_once_with(timeout=5)

    def test_missing_command_does_not_start_servers(self):
        with (
            patch.object(sys, "argv", ["reqlica"]),
            self.assertRaises(SystemExit) as raised,
        ):
            main()

        self.assertEqual(raised.exception.code, 2)
        self.launch.assert_not_called()

    def test_project_create_prints_registered_mock_url_without_starting_servers(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "books"
            output = StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "reqlica",
                        "project",
                        "create",
                        str(directory),
                        "--name",
                        "Book API",
                    ],
                ),
                patch.dict(
                    os.environ,
                    {
                        "REQLICA_HOME": str(Path(temporary) / "home"),
                        "MOCK_HOST": "127.0.0.2",
                        "MOCK_PORT": "4100",
                    },
                ),
                patch("reqlica.cli.load_dotenv") as load_env,
                patch("sys.stdout", output),
            ):
                self.assertEqual(main(), 0)
                project_id = json.loads((directory / "project.json").read_text())["id"]
                self.assertEqual(get_project(project_id).name, "Book API")
            self.assertIn(
                f"http://127.0.0.2:4100/mock/{project_id}/hello", output.getvalue()
            )
            load_env.assert_called_once_with(
                Path(__file__).resolve().parents[1] / ".env"
            )
            self.launch.assert_not_called()

    def test_project_create_refuses_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "keep.txt").write_text("untouched")
            with (
                patch.object(sys, "argv", ["reqlica", "project", "create", temporary]),
                patch("reqlica.cli.load_dotenv"),
                patch("sys.stderr", StringIO()) as errors,
            ):
                self.assertEqual(main(), 1)
            self.assertIn("must be empty", errors.getvalue())
            self.assertEqual((directory / "keep.txt").read_text(), "untouched")
            self.launch.assert_not_called()
