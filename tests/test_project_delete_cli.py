"""Deletion prompts are safe by default and never fall back to offline file removal."""

import json
import os
import sys
import unittest
from io import StringIO
from unittest.mock import patch

import httpx
from reqlica.cli import main


class ProjectDeleteCliTests(unittest.TestCase):
    project_id = "a" * 32

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch("reqlica.cli.load_dotenv"))
        self.info = {
            "id": self.project_id,
            "name": "Todo",
            "directory": "/projects/todo",
            "registered": True,
            "delete_files": False,
            "error": None,
        }
        self.request = self.enterContext(
            patch("reqlica.cli.httpx.request", side_effect=self.reply)
        )

    def reply(self, method, url, **kwargs):
        if url.endswith("/deletion"):
            return httpx.Response(
                200,
                json={**self.info, "delete_files": kwargs["params"]["delete_files"]},
            )
        if method == "GET":
            return httpx.Response(200, json={"projects": [self.info]})
        return httpx.Response(
            200,
            json={
                "id": self.project_id,
                "name": "Todo",
                "directory": "/projects/todo",
                "status": "deleted",
                "unregistered": True,
                "files_deleted": kwargs["json"]["delete_files"],
            },
        )

    def run_command(self, *arguments):
        output, errors = StringIO(), StringIO()
        with (
            patch.object(sys, "argv", ["reqlica", "project", "delete", *arguments]),
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
        ):
            result = main()
        return result, output.getvalue(), errors.getvalue()

    def test_default_is_confirmed_unregistration_not_file_deletion(self):
        with patch("builtins.input", return_value="yes"):
            result, output, errors = self.run_command(self.project_id, "--json")
        self.assertEqual(result, 0, errors)
        self.assertFalse(json.loads(output)["files_deleted"])
        self.assertIn("keep its files", errors)
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(self.request.call_args.args[0], "DELETE")
        self.assertEqual(
            self.request.call_args.kwargs["json"],
            {"delete_files": False, "expected_directory": None},
        )

    def test_delete_files_is_explicit_and_uses_confirmed_directory(self):
        with patch("builtins.input") as prompt:
            result, output, errors = self.run_command(
                self.project_id, "--delete-files", "--yes", "--json"
            )
        self.assertEqual((result, errors), (0, ""))
        prompt.assert_not_called()
        self.assertTrue(json.loads(output)["files_deleted"])
        self.assertEqual(
            self.request.call_args.kwargs["json"],
            {"delete_files": True, "expected_directory": "/projects/todo"},
        )

    def test_no_empty_answer_and_eof_never_delete(self):
        for answer in ("n", "", EOFError()):
            with (
                self.subTest(answer=answer),
                patch(
                    "builtins.input",
                    side_effect=answer if isinstance(answer, Exception) else None,
                    return_value=answer,
                ),
            ):
                self.request.reset_mock()
                result, output, errors = self.run_command(
                    self.project_id, "--delete-files"
                )
                self.assertEqual((result, output), (1, ""))
                self.assertIn("no changes", errors)
                self.assertEqual(self.request.call_count, 1)
                self.assertEqual(self.request.call_args.args[0], "GET")

    def test_names_and_prefixes_resolve_from_gateway_not_local_registry(self):
        for reference in ("Todo", "todo", self.project_id[:6]):
            with self.subTest(reference=reference):
                self.request.reset_mock()
                result, output, errors = self.run_command(reference, "--yes")
                self.assertEqual((result, errors), (0, ""))
                self.assertIn("files kept", output)
                self.assertEqual(self.request.call_count, 3)
                self.assertTrue(
                    self.request.call_args.args[1].endswith(self.project_id)
                )

    def test_ambiguity_and_unsafe_file_target_never_send_delete(self):
        self.request.side_effect = lambda *args, **kwargs: httpx.Response(
            200, json={"projects": [self.info, {**self.info, "id": "b" * 32}]}
        )
        result, _, errors = self.run_command("Todo", "--yes")
        self.assertEqual(result, 1)
        self.assertIn("ambiguous", errors)
        self.assertEqual(self.request.call_count, 1)
        self.request.reset_mock()
        self.info["directory"] = None
        self.request.side_effect = self.reply
        result, _, errors = self.run_command(self.project_id, "--delete-files", "--yes")
        self.assertEqual(result, 1)
        self.assertIn("confirmed absolute", errors)
        self.assertEqual(self.request.call_count, 1)

    def test_gateway_failure_does_not_trigger_local_deletion(self):
        for response in (
            httpx.Response(409, json={"detail": "Project directory changed"}),
            httpx.Response(404, json={"detail": "Not Found"}),
            httpx.Response(200, json=[]),
        ):
            with self.subTest(response=response):
                self.request.return_value = response
                self.request.side_effect = None
                self.request.reset_mock()
                result, output, errors = self.run_command(
                    self.project_id, "--delete-files", "--yes"
                )
                self.assertEqual((result, output), (1, ""))
                self.assertIn("Could not delete", errors)
                self.assertEqual(self.request.call_count, 1)
        self.request.side_effect = httpx.ConnectError("offline")
        result, _, errors = self.run_command(self.project_id, "--yes")
        self.assertEqual(result, 1)
        self.assertIn("reqlica start", errors)
