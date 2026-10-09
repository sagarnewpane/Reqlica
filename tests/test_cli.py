"""Check CLI server supervision and gateway-backed project management."""

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

import httpx
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


class ProjectManagementCliTests(unittest.TestCase):
    project_id = "a" * 32

    def setUp(self):
        self.project = {
            "id": self.project_id,
            "name": "Book API",
            "directory": "/projects/books",
            "status": "running",
            "pid": 1234,
            "error": None,
            "url": f"http://127.0.0.1:4000/mock/{self.project_id}",
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/hello",
                    "handler": "endpoints/hello.py:hello",
                    "url": f"http://127.0.0.1:4000/mock/{self.project_id}/hello",
                }
            ],
        }
        self.request = self.enterContext(patch("reqlica.cli.httpx.request"))
        self.load_env = self.enterContext(patch("reqlica.cli.load_dotenv"))
        self.launch = self.enterContext(patch("reqlica.cli.subprocess.Popen"))
        self.create = self.enterContext(patch("reqlica.cli.create_project"))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def run_command(self, *arguments):
        output, errors = StringIO(), StringIO()
        with (
            patch.object(sys, "argv", ["reqlica", "project", *arguments]),
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
        ):
            result = main()
        self.launch.assert_not_called()
        self.create.assert_not_called()
        return result, output.getvalue(), errors.getvalue()

    def reply(self, payload, status=200):
        self.request.return_value = httpx.Response(status, json=payload)

    def test_all_command_methods_urls_and_readable_output(self):
        for command in ("list", "endpoints", "start", "stop"):
            with self.subTest(command=command):
                self.reply(
                    {"projects": [self.project]} if command == "list" else self.project
                )
                arguments = (
                    [command] if command == "list" else [command, self.project_id]
                )
                result, output, errors = self.run_command(*arguments)
                self.assertEqual(result, 0)
                self.assertEqual(errors, "")
                self.assertIn(f"Book API ({self.project_id}) [running]", output)
                self.assertIn("PID: 1234", output)
                self.assertIn("Directory: /projects/books", output)
                self.assertIn("GET " + self.project["endpoints"][0]["url"], output)
                self.assertIn(self.project["endpoints"][0]["handler"], output)
                route = "/api/projects"
                if command != "list":
                    route += f"/{self.project_id}/{command}"
                self.request.assert_called_with(
                    "POST" if command in {"start", "stop"} else "GET",
                    "http://127.0.0.1:4000" + route,
                    trust_env=False,
                    timeout=30.0,
                )
                self.load_env.assert_called_with(
                    Path(__file__).resolve().parents[1] / ".env"
                )

    def test_json_is_exact_api_payload_for_every_command(self):
        self.project["extra"] = {"preserved": True}
        for command in ("list", "endpoints", "start", "stop"):
            with self.subTest(command=command):
                payload = (
                    {"projects": [self.project]} if command == "list" else self.project
                )
                self.reply(payload)
                arguments = (
                    [command] if command == "list" else [command, self.project_id]
                )
                result, output, errors = self.run_command(*arguments, "--json")
                self.assertEqual(result, 0)
                self.assertEqual(json.loads(output), payload)
                self.assertEqual(errors, "")

    def test_running_filter_is_delegated_to_gateway(self):
        self.reply({"projects": [self.project]})
        result, output, errors = self.run_command("list", "--running", "--json")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output), {"projects": [self.project]})
        self.assertEqual(errors, "")
        self.request.assert_called_once_with(
            "GET",
            "http://127.0.0.1:4000/api/projects?running=true",
            trust_env=False,
            timeout=30.0,
        )

    def test_empty_lists(self):
        self.reply({"projects": []})
        for arguments, expected in (
            (("list",), "No projects registered."),
            (("list", "--running"), "No running projects."),
            (("list", "--json"), '{"projects": []}'),
        ):
            with self.subTest(arguments=arguments):
                result, output, errors = self.run_command(*arguments)
                self.assertEqual(result, 0)
                if "--json" in arguments:
                    self.assertEqual(json.loads(output), json.loads(expected))
                else:
                    self.assertIn(expected, output)
                self.assertEqual(errors, "")

    def test_list_shows_faulty_registry_entry(self):
        faulty = {
            **self.project,
            "status": "error",
            "name": None,
            "directory": None,
            "pid": None,
            "error": "Cannot open project: malformed manifest",
            "endpoints": [],
        }
        self.reply({"projects": [self.project, faulty]})
        result, output, errors = self.run_command("list")
        self.assertEqual(result, 0)
        self.assertIn(f"(unnamed) ({self.project_id}) [error]", output)
        self.assertIn(faulty["error"], output)
        self.assertIn("No endpoints.", output)
        self.assertEqual(errors, "")

    def test_idle_and_stopped_statuses(self):
        for status in ("idle", "stopped"):
            with self.subTest(status=status):
                self.reply({**self.project, "status": status, "pid": None})
                result, output, errors = self.run_command("endpoints", self.project_id)
                self.assertEqual(result, 0)
                self.assertIn(f"[{status}]", output)
                self.assertNotIn("PID:", output)
                self.assertEqual(errors, "")

    def test_unreachable_gateway_and_timeout(self):
        for error in (
            httpx.ConnectError("Connection refused"),
            httpx.ReadTimeout("Timed out"),
        ):
            with self.subTest(error=error):
                self.request.side_effect = error
                result, output, errors = self.run_command("list", "--json")
                self.assertEqual(result, 1)
                self.assertEqual(output, "")
                self.assertIn("Run 'reqlica start' first", errors)
                self.assertNotIn("Traceback", errors)

    def test_json_detail_errors(self):
        for status in (404, 422, 502, 503):
            with self.subTest(status=status):
                self.reply({"detail": "Project operation failed"}, status)
                result, output, errors = self.run_command(
                    "start", self.project_id, "--json"
                )
                self.assertEqual(result, 1)
                self.assertEqual(output, "")
                self.assertIn(str(status), errors)
                self.assertIn("Project operation failed", errors)
                if status == 404:
                    self.assertIn("restart 'reqlica start'", errors)

    def test_old_gateway_list_404_suggests_restart(self):
        self.reply({"detail": "Not Found"}, 404)
        result, output, errors = self.run_command("list")
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("restart 'reqlica start'", errors)

    def test_non_json_error_reply(self):
        self.request.return_value = httpx.Response(502, text="<html>Bad gateway</html>")
        result, output, errors = self.run_command("list")
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("502", errors)
        self.assertIn("No JSON error detail", errors)

    def test_malformed_success_payloads(self):
        for payload in (
            None,
            [],
            {},
            {"projects": None},
            {"projects": [None]},
            {"projects": [{}]},
            {"projects": [self.project, {}]},
            {"projects": [{**self.project, "id": "../unsafe"}]},
            {"projects": [{**self.project, "status": "unknown"}]},
            {"projects": [{**self.project, "name": []}]},
            {"projects": [{**self.project, "pid": True}]},
            {"projects": [{**self.project, "url": "/relative"}]},
            {"projects": [{**self.project, "endpoints": None}]},
            {"projects": [{**self.project, "endpoints": [None]}]},
            {
                "projects": [
                    {**self.project, "endpoints": [{"url": "http://localhost"}]}
                ]
            },
        ):
            with self.subTest(payload=payload):
                self.reply(payload)
                result, output, errors = self.run_command("list", "--json")
                self.assertEqual(result, 1)
                self.assertEqual(output, "")
                self.assertIn("Malformed mock gateway reply", errors)

    def test_non_json_success_and_malformed_single_project(self):
        for response in (
            httpx.Response(200, text="not JSON"),
            httpx.Response(200, json={}),
        ):
            with self.subTest(response=response):
                self.request.return_value = response
                result, output, errors = self.run_command("endpoints", self.project_id)
                self.assertEqual(result, 1)
                self.assertEqual(output, "")
                self.assertIn("Malformed mock gateway reply", errors)

    def test_invalid_ids_never_make_requests(self):
        for command in ("endpoints", "start", "stop"):
            for project_id in (
                "../other",
                "demo",
                "A" * 32,
                "a" * 31,
                "a" * 33,
                "g" * 32,
                "a" * 32 + "/stop",
                "a" * 32 + "\n",
            ):
                with self.subTest(command=command, project_id=project_id):
                    result, output, errors = self.run_command(command, project_id)
                    self.assertEqual(result, 1)
                    self.assertEqual(output, "")
                    self.assertIn("Invalid project ID", errors)
        self.request.assert_not_called()

    def test_configured_hosts_wildcards_and_ipv6(self):
        self.reply({"projects": []})
        for host, url_host in (
            ("127.0.0.2", "127.0.0.2"),
            ("localhost", "localhost"),
            ("0.0.0.0", "127.0.0.1"),
            ("::", "[::1]"),
            ("[::]", "[::1]"),
            ("::1", "[::1]"),
            ("2001:db8::1", "[2001:db8::1]"),
            ("[::1]", "[::1]"),
        ):
            with (
                self.subTest(host=host),
                patch.dict(os.environ, {"MOCK_HOST": host, "MOCK_PORT": "4100"}),
            ):
                result, _, errors = self.run_command("list")
                self.assertEqual(result, 0)
                self.assertEqual(errors, "")
                self.request.assert_called_with(
                    "GET",
                    f"http://{url_host}:4100/api/projects",
                    trust_env=False,
                    timeout=30.0,
                )

    def test_invalid_gateway_configuration(self):
        self.request.side_effect = httpx.InvalidURL("Invalid port")
        result, output, errors = self.run_command("list")
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("Invalid mock gateway configuration", errors)
