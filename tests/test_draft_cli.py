"""Check console-backed editing and explicitly local human approval."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import call, patch

import httpx
from reqlica.cli import main
from reqlica.projects import drafts
from reqlica.projects.store import create_project


class DraftCliTests(unittest.TestCase):
    project_id = "a" * 32
    draft_id = "b" * 32
    revision = "c" * 64

    def setUp(self):
        self.request = self.enterContext(patch("reqlica.cli.httpx.request"))
        self.enterContext(patch("reqlica.cli.load_dotenv"))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.payload = {
            "id": self.draft_id,
            "revision": self.revision,
            "extra": {"keep": [1, {"nested": True}]},
        }
        self.request.return_value = httpx.Response(200, json=self.payload)

    def run_command(self, *arguments):
        output, errors = StringIO(), StringIO()
        with (
            patch.object(sys, "argv", ["reqlica", *arguments]),
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
        ):
            result = main()
        return result, output.getvalue(), errors.getvalue()

    def test_create_list_and_inspect(self):
        for command, suffix, ids in (
            ("create", "", [self.project_id]),
            ("list", "", [self.project_id]),
            ("inspect", "/" + self.draft_id, [self.project_id, self.draft_id]),
        ):
            with self.subTest(command=command):
                result, output, errors = self.run_command(
                    "draft", command, *ids, "--json"
                )
                self.assertEqual((result, errors), (0, ""))
                self.assertEqual(json.loads(output), self.payload)
                self.request.assert_called_with(
                    "POST" if command == "create" else "GET",
                    f"http://127.0.0.1:3000/api/projects/{self.project_id}/drafts{suffix}",
                    json=None,
                    timeout=60.0,
                    trust_env=False,
                )

    def test_write_reads_utf8_file_and_delete_uses_query_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.py"
            source.write_text(
                "def hello():\n    return {'ok': True}\n", encoding="utf-8"
            )
            result, output, errors = self.run_command(
                "draft",
                "write",
                self.project_id,
                self.draft_id,
                "endpoints/hello.py",
                "--file",
                str(source),
                "--revision",
                self.revision,
                "--json",
            )
            self.assertEqual((result, errors), (0, ""))
            self.assertEqual(json.loads(output), self.payload)
            self.request.assert_called_once_with(
                "PUT",
                f"http://127.0.0.1:3000/api/projects/{self.project_id}/drafts/{self.draft_id}/files",
                params={"path": "endpoints/hello.py"},
                json={
                    "expected_revision": self.revision,
                    "content": source.read_text(),
                },
                timeout=60.0,
                trust_env=False,
            )
        result, _, errors = self.run_command(
            "draft",
            "delete",
            self.project_id,
            self.draft_id,
            "endpoints/hello.py",
            "--revision",
            self.revision,
        )
        self.assertEqual((result, errors), (0, ""))
        self.assertEqual(self.request.call_args.args[0], "DELETE")
        self.assertEqual(
            self.request.call_args.kwargs["params"], {"path": "endpoints/hello.py"}
        )

    def test_endpoint_commands_and_nested_call_payload(self):
        for command, extra, method, body in (
            (
                "upsert",
                ["POST", "/books", "endpoints/books.py:create"],
                "PUT",
                {
                    "endpoint": {
                        "method": "POST",
                        "path": "/books",
                        "handler": "endpoints/books.py:create",
                    }
                },
            ),
            (
                "remove",
                ["POST", "/books"],
                "DELETE",
                {"method": "POST", "path": "/books"},
            ),
            (
                "call",
                ["POST", "/books", "--body", '{"items":[{"name":"book"}]}'],
                "POST",
                {
                    "method": "POST",
                    "path": "/books",
                    "json_body": {"items": [{"name": "book"}]},
                },
            ),
        ):
            with self.subTest(command=command):
                payload = {
                    **self.payload,
                    "status": "passed",
                    "response": {"json": {"nested": [1]}, "status_code": 200},
                }
                self.request.return_value = httpx.Response(200, json=payload)
                result, output, errors = self.run_command(
                    "draft",
                    command,
                    self.project_id,
                    self.draft_id,
                    *extra,
                    "--revision",
                    self.revision,
                    "--json",
                )
                self.assertEqual((result, errors), (0, ""))
                self.assertEqual(json.loads(output), payload)
                suffix = "call" if command == "call" else "endpoints"
                self.request.assert_called_with(
                    method,
                    f"http://127.0.0.1:3000/api/projects/{self.project_id}/drafts/{self.draft_id}/{suffix}",
                    json={"expected_revision": self.revision, **body},
                    timeout=60.0,
                    trust_env=False,
                )

    def test_failed_checks_return_nonzero_without_changing_payload(self):
        for command in ("validate", "test"):
            for status in ("passed", "failed", "error", "timeout"):
                with self.subTest(command=command, status=status):
                    payload = {
                        "status": status,
                        "revision": self.revision,
                        "output": "check log",
                        "tests_run": 1,
                    }
                    self.request.return_value = httpx.Response(200, json=payload)
                    result, output, errors = self.run_command(
                        "draft",
                        command,
                        self.project_id,
                        self.draft_id,
                        "--revision",
                        self.revision,
                        "--json",
                    )
                    self.assertEqual(result, 0 if status == "passed" else 1)
                    self.assertEqual(json.loads(output), payload)
                    self.assertEqual(bool(errors), status != "passed")

    def test_checks_and_calls_resolve_current_revision_without_manual_hash(self):
        for command, extra in (
            ("validate", []),
            ("test", []),
            ("call", ["POST", "/hello", "--body", '{"name":"Sagar"}']),
        ):
            with self.subTest(command=command):
                self.request.reset_mock()
                self.request.side_effect = [
                    httpx.Response(200, json=self.payload),
                    httpx.Response(
                        200, json={"status": "passed", "revision": self.revision}
                    ),
                ]
                result, _, errors = self.run_command(
                    "draft", command, self.project_id, self.draft_id, *extra, "--json"
                )
                self.assertEqual((result, errors), (0, ""))
                root = f"http://127.0.0.1:3000/api/projects/{self.project_id}/drafts/{self.draft_id}"
                body = {"expected_revision": self.revision}
                if command == "call":
                    body.update(
                        method="POST", path="/hello", json_body={"name": "Sagar"}
                    )
                self.assertEqual(
                    self.request.call_args_list,
                    [
                        call("GET", root, trust_env=False, timeout=60.0),
                        call(
                            "POST",
                            root + "/" + command,
                            json=body,
                            trust_env=False,
                            timeout=60.0,
                        ),
                    ],
                )

    def test_automatic_revision_lookup_rejects_errors_and_malformed_replies(self):
        for response in (
            httpx.Response(404, json={"detail": "Unknown draft"}),
            httpx.Response(200, json=[]),
            httpx.Response(200, json={"revision": "placeholder"}),
            httpx.Response(200, json={}),
        ):
            with self.subTest(response=response):
                self.request.reset_mock()
                self.request.return_value = response
                result, output, errors = self.run_command(
                    "draft", "test", self.project_id, self.draft_id, "--json"
                )
                self.assertEqual((result, output), (1, ""))
                self.assertTrue(errors)
                self.assertEqual(self.request.call_count, 1)
                self.assertEqual(self.request.call_args.args[0], "GET")

    def test_automatic_revision_conflicts_are_not_silently_retried(self):
        self.request.side_effect = [
            httpx.Response(200, json=self.payload),
            httpx.Response(409, json={"detail": "Draft revision conflict"}),
        ]
        result, _, errors = self.run_command(
            "draft", "test", self.project_id, self.draft_id
        )
        self.assertEqual(result, 1)
        self.assertIn("revision conflict", errors)
        self.assertEqual(self.request.call_count, 2)

    def test_short_references_and_url_command(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(Path(temporary) / "home")})
        )
        project = create_project(Path(temporary) / "todo", "Todo API")
        draft = drafts.create_draft(project.id)
        for reference in (project.name, project.id[:8]):
            with self.subTest(reference=reference):
                self.request.reset_mock()
                self.request.side_effect = [
                    httpx.Response(200, json=draft),
                    httpx.Response(200, json={"status": "passed"}),
                ]
                result, _, errors = self.run_command(
                    "draft", "test", reference, draft["id"][:8]
                )
                self.assertEqual((result, errors), (0, ""))
                self.assertIn(
                    f"/{project.id}/drafts/{draft['id']}",
                    self.request.call_args.args[1],
                )
                self.assertEqual(
                    self.request.call_args.kwargs["json"]["expected_revision"],
                    draft["revision"],
                )
        self.request.reset_mock(side_effect=True)
        result, output, errors = self.run_command(
            "draft", "url", project.name, draft["id"][:8], "/hello", "--json"
        )
        self.assertEqual((result, errors), (0, ""))
        payload = json.loads(output)
        self.assertEqual(payload["kind"], "draft_preview")
        self.assertTrue(payload["endpoints"][0]["url"].endswith("/preview/hello"))
        result, output, errors = self.run_command(
            "draft", "url", project.id[:8], draft["id"][:8], "/hello"
        )
        self.assertEqual((result, errors), (0, ""))
        self.assertTrue(output.startswith("GET http://127.0.0.1:3000/"))
        create_project(Path(temporary) / "duplicate", "Todo API")
        result, _, errors = self.run_command(
            "draft", "test", "Todo API", draft["id"][:8]
        )
        self.assertEqual(result, 1)
        self.assertIn("Ambiguous", errors)
        self.request.assert_not_called()

    def test_activate_uses_configured_mock_gateway_and_sixty_seconds(self):
        payload = {**self.payload, "status": "running", "active_version": "d" * 32}
        self.request.return_value = httpx.Response(200, json=payload)
        with patch.dict(os.environ, {"MOCK_HOST": "::", "MOCK_PORT": "4100"}):
            result, output, errors = self.run_command(
                "draft",
                "activate",
                self.project_id,
                self.draft_id,
                "--revision",
                self.revision,
                "--json",
            )
        self.assertEqual((result, errors), (0, ""))
        self.assertEqual(json.loads(output), payload)
        self.request.assert_called_once_with(
            "POST",
            f"http://[::1]:4100/api/projects/{self.project_id}/activate",
            json={"draft_id": self.draft_id, "expected_revision": self.revision},
            timeout=60.0,
            trust_env=False,
        )

    def test_configured_console_wildcard_and_ipv6(self):
        with patch.dict(os.environ, {"CONSOLE_HOST": "::", "CONSOLE_PORT": "3100"}):
            self.assertEqual(self.run_command("draft", "create", self.project_id)[0], 0)
        self.assertEqual(
            self.request.call_args.args[1],
            f"http://[::1]:3100/api/projects/{self.project_id}/drafts",
        )

    def test_api_and_local_io_failures_are_useful(self):
        for status in (404, 409, 422, 502):
            with self.subTest(status=status):
                self.request.return_value = httpx.Response(
                    status, json={"detail": "operation failed"}
                )
                result, output, errors = self.run_command(
                    "draft", "create", self.project_id, "--json"
                )
                self.assertEqual((result, output), (1, ""))
                self.assertIn(str(status), errors)
                self.assertIn("operation failed", errors)
        self.request.side_effect = httpx.ReadTimeout("timed out")
        result, _, errors = self.run_command("draft", "create", self.project_id)
        self.assertEqual(result, 1)
        self.assertIn("timed out", errors)
        self.request.reset_mock(side_effect=True)
        result, _, errors = self.run_command(
            "draft",
            "write",
            self.project_id,
            self.draft_id,
            "file.py",
            "--file",
            "/missing/reqlica-file",
            "--revision",
            self.revision,
        )
        self.assertEqual(result, 1)
        self.assertIn("Could not write draft", errors)
        self.request.assert_not_called()

    def test_invalid_ids_and_missing_revision_do_not_request(self):
        self.assertEqual(self.run_command("draft", "create", "../bad")[0], 1)
        self.assertEqual(
            self.run_command("draft", "inspect", self.project_id, "bad")[0], 1
        )
        self.assertEqual(
            self.run_command(
                "draft", "test", self.project_id, self.draft_id, "--revision", "bad"
            )[0],
            1,
        )
        for command in (
            "write",
            "delete",
            "upsert",
            "remove",
            "approve",
            "activate",
        ):
            with self.subTest(command=command), self.assertRaises(SystemExit) as raised:
                self.run_command("draft", command, self.project_id, self.draft_id)
            self.assertEqual(raised.exception.code, 2)
        self.request.assert_not_called()

    def test_approval_is_local_and_declines_on_no_or_eof(self):
        state = {
            **self.payload,
            "changes": {"modified": ["hello.py"]},
            "validation": {"status": "passed"},
            "test": {"status": "passed", "tests_run": 1},
        }
        with (
            patch("reqlica.cli.drafts.inspect_draft", return_value=state),
            patch("reqlica.cli.drafts.approve_draft", return_value=state) as approve,
        ):
            for answer in ("n", "", EOFError()):
                with (
                    self.subTest(answer=answer),
                    patch(
                        "builtins.input",
                        side_effect=answer if isinstance(answer, Exception) else None,
                        return_value=answer,
                    ),
                ):
                    result, output, errors = self.run_command(
                        "draft",
                        "approve",
                        self.project_id,
                        self.draft_id,
                        "--revision",
                        self.revision,
                        "--json",
                    )
                self.assertEqual((result, output), (1, ""))
                self.assertIn(self.revision, errors)
                self.assertIn("hello.py", errors)
                self.assertIn("tests_run", errors)
            approve.assert_not_called()
            with patch("builtins.input", return_value="yes"):
                result, _, _ = self.run_command(
                    "draft",
                    "approve",
                    self.project_id,
                    self.draft_id,
                    "--revision",
                    self.revision,
                )
                self.assertEqual(result, 0)
            with patch("builtins.input") as prompt:
                result, output, errors = self.run_command(
                    "draft",
                    "approve",
                    self.project_id,
                    self.draft_id,
                    "--revision",
                    self.revision,
                    "--yes",
                    "--json",
                )
            self.assertEqual((result, errors), (0, ""))
            self.assertEqual(json.loads(output), state)
            prompt.assert_not_called()
            approve.assert_called_with(self.project_id, self.draft_id, self.revision)
        self.request.assert_not_called()

    def test_convert_is_gateway_backed_and_preserves_active_version(self):
        payload = {
            "id": self.project_id,
            "name": "Books",
            "directory": "/books",
            "status": "stopped",
            "pid": None,
            "error": None,
            "url": f"http://127.0.0.1:4000/mock/{self.project_id}",
            "endpoints": [],
            "active_version": "d" * 32,
        }
        self.request.return_value = httpx.Response(200, json=payload)
        result, output, errors = self.run_command(
            "project", "convert", self.project_id, "--json"
        )
        self.assertEqual((result, errors), (0, ""))
        self.assertEqual(json.loads(output), payload)
        self.request.assert_called_once_with(
            "POST",
            f"http://127.0.0.1:4000/api/projects/{self.project_id}/convert",
            timeout=30.0,
            trust_env=False,
        )

    def test_approval_never_approves_a_stale_revision(self):
        with (
            patch(
                "reqlica.cli.drafts.inspect_draft", return_value={"revision": "d" * 64}
            ),
            patch("reqlica.cli.drafts.approve_draft") as approve,
            patch("builtins.input") as prompt,
        ):
            result, output, errors = self.run_command(
                "draft",
                "approve",
                self.project_id,
                self.draft_id,
                "--revision",
                self.revision,
                "--yes",
                "--json",
            )
        self.assertEqual((result, output), (1, ""))
        self.assertIn("revision conflict", errors)
        approve.assert_not_called()
        prompt.assert_not_called()
        self.request.assert_not_called()


class LocalDraftApprovalTests(unittest.TestCase):
    def test_approval_requires_real_checks_and_loads_registry_from_dotenv(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = str(Path(temporary) / "home")
            with patch.dict(os.environ, {"REQLICA_HOME": home}):
                project = create_project(Path(temporary) / "books")
                state = drafts.create_draft(project.id)
            arguments = [
                "reqlica",
                "draft",
                "approve",
                project.id,
                state["id"],
                "--revision",
                state["revision"],
                "--yes",
                "--json",
            ]
            with (
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "reqlica.cli.load_dotenv",
                    side_effect=lambda _: os.environ.update(REQLICA_HOME=home),
                ) as load,
                patch("reqlica.cli.httpx.request") as request,
                patch.object(sys, "argv", arguments),
                patch("sys.stdout", StringIO()) as output,
                patch("sys.stderr", StringIO()) as errors,
            ):
                self.assertEqual(main(), 1)
                self.assertIn("Validation and tests must pass", errors.getvalue())
                self.assertIsNone(
                    drafts.inspect_draft(project.id, state["id"])["approval"]
                )
                validation = drafts.validate_draft(
                    project.id, state["id"], state["revision"]
                )
                self.assertEqual(validation["status"], "passed", validation["output"])
                result = drafts.test_draft(project.id, state["id"], state["revision"])
                self.assertGreaterEqual(result["tests_run"], 1, result["output"])
                self.assertEqual(main(), 0)
                self.assertEqual(
                    json.loads(output.getvalue())["approval"],
                    {"revision": state["revision"]},
                )
                load.assert_called_with(Path(__file__).resolve().parents[1] / ".env")
                request.assert_not_called()
