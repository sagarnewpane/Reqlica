"""Check console startup, domain draft adapters, and gateway forwarding."""

import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from reqlica.console.app import app, create_app, main
from reqlica.projects.store import create_project


class ConsoleTests(unittest.TestCase):
    def test_health(self):
        with TestClient(create_app()) as client:
            response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"status": "ok", "service": "reqlica-console"}
        )

    def test_startup_defaults(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("reqlica.console.app.load_dotenv") as load_env,
            patch("reqlica.console.app.uvicorn.run") as run_server,
        ):
            main()

        load_env.assert_called_once_with(Path(__file__).resolve().parents[1] / ".env")
        run_server.assert_called_once_with(app, host="127.0.0.1", port=3000)

    def test_startup_uses_loaded_settings(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "reqlica.console.app.load_dotenv",
                side_effect=lambda _: load_dotenv(
                    stream=StringIO("CONSOLE_HOST=127.0.0.2\nCONSOLE_PORT=3100\n")
                ),
            ),
            patch("reqlica.console.app.uvicorn.run") as run_server,
        ):
            main()

        run_server.assert_called_once_with(app, host="127.0.0.2", port=3100)


class DraftConsoleTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(temporary)
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.directory / "home")})
        )
        self.project = create_project(self.directory / "books")
        self.client = self.enterContext(TestClient(create_app()))
        self.root = f"/api/projects/{self.project.id}"
        response = self.client.post(self.root + "/drafts")
        self.assertEqual(response.status_code, 200, response.text)
        self.draft = response.json()
        self.route = self.root + "/drafts/" + self.draft["id"]

    def test_list_inspect_edit_read_delete_and_revision_conflict(self):
        self.assertEqual(
            self.client.get(self.root + "/drafts").json(), {"drafts": [self.draft]}
        )
        self.assertEqual(self.client.get(self.route).json(), self.draft)
        body = {"expected_revision": self.draft["revision"], "content": "notes\n"}
        response = self.client.put(
            self.route + "/files", params={"path": "notes.txt"}, json=body
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertNotEqual(updated["revision"], self.draft["revision"])
        self.assertEqual(updated["changes"]["added"], ["notes.txt"])
        self.assertEqual(
            self.client.get(self.route + "/files", params={"path": "notes.txt"}).json(),
            {
                "path": "notes.txt",
                "content": "notes\n",
                "revision": updated["revision"],
            },
        )
        response = self.client.put(
            self.route + "/files", params={"path": "notes.txt"}, json=body
        )
        self.assertEqual(response.status_code, 409)
        response = self.client.request(
            "DELETE",
            self.route + "/files",
            params={"path": "notes.txt"},
            json={"expected_revision": updated["revision"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], self.draft["revision"])
        response = self.client.get(self.route + "/files", params={"path": "notes.txt"})
        self.assertEqual(response.status_code, 404)

    def test_endpoint_upsert_and_remove_use_domain_manifest(self):
        response = self.client.put(
            self.route + "/endpoints",
            json={
                "expected_revision": self.draft["revision"],
                "endpoint": {
                    "method": "GET",
                    "path": "/other",
                    "handler": "endpoints/hello.py:hello",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["changes"]["modified"], ["manifest.json"])
        response = self.client.request(
            "DELETE",
            self.route + "/endpoints",
            json={
                "expected_revision": updated["revision"],
                "method": "GET",
                "path": "/other",
            },
        )
        self.assertEqual(response.status_code, 200)
        manifest = self.client.get(
            self.route + "/files", params={"path": "manifest.json"}
        ).json()["content"]
        self.assertNotIn("/other", manifest)

    def test_safe_reads_and_missing_resources(self):
        for path in ("../project.json", "/etc/passwd", "endpoints/../../project.json"):
            with self.subTest(path=path):
                response = self.client.get(self.route + "/files", params={"path": path})
                self.assertEqual(response.status_code, 409)
        outside = self.directory / "secret.txt"
        outside.write_text("secret")
        link = self.project.directory / "drafts" / self.draft["id"] / "link.txt"
        link.symlink_to(outside)
        response = self.client.get(self.route + "/files", params={"path": "link.txt"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('"content"', response.text)
        self.assertEqual(
            self.client.get(self.root + "/drafts/" + "f" * 32).status_code, 404
        )
        response = self.client.post("/api/projects/" + "f" * 32 + "/drafts")
        self.assertEqual(response.status_code, 404)

    def test_real_checks_and_call_use_starter_endpoint_and_test(self):
        revision = {"expected_revision": self.draft["revision"]}
        for command in ("validate", "test"):
            response = self.client.post(self.route + "/" + command, json=revision)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "passed", response.text)
            self.assertEqual(response.json()["revision"], self.draft["revision"])
            if command == "test":
                self.assertGreaterEqual(response.json()["tests_run"], 1)
        response = self.client.post(
            self.route + "/call",
            json={**revision, "method": "GET", "path": "/hello"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "passed", response.text)
        self.assertEqual(response.json()["status_code"], 200)
        self.assertEqual(response.json()["body"]["project_id"], self.project.id)

    def test_call_returns_exact_nested_domain_result(self):
        revision = {"expected_revision": self.draft["revision"]}
        payload = {
            "status": "passed",
            "revision": self.draft["revision"],
            "response": {
                "status_code": 201,
                "json": {"nested": [1, {"keep": True}]},
                "headers": {"x-test": "yes"},
            },
            "output": "logs",
        }
        with patch(
            "reqlica.projects.drafts.call_draft_endpoint", return_value=payload
        ) as call:
            response = self.client.post(
                self.route + "/call",
                json={
                    **revision,
                    "method": "POST",
                    "path": "/hello",
                    "json_body": {"data": [1]},
                },
            )
        self.assertEqual(response.json(), payload)
        call.assert_called_once_with(
            self.project.id,
            self.draft["id"],
            self.draft["revision"],
            "POST",
            "/hello",
            {"data": [1]},
        )
        self.assertIsNone(self.client.get(self.route).json()["approval"])

    def test_failed_check_is_a_result_not_an_http_error(self):
        payload = {
            "status": "failed",
            "revision": self.draft["revision"],
            "output": "syntax error",
            "tests_run": 0,
        }
        with patch("reqlica.projects.drafts.validate_draft", return_value=payload):
            response = self.client.post(
                self.route + "/validate",
                json={"expected_revision": self.draft["revision"]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)

    def test_revision_is_required_and_approval_is_not_exposed(self):
        for command in ("validate", "test", "call"):
            response = self.client.post(self.route + "/" + command, json={})
            self.assertEqual(response.status_code, 422)
        response = self.client.post(
            self.route + "/approve", json={"expected_revision": self.draft["revision"]}
        )
        self.assertEqual(response.status_code, 404)
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertFalse(any("approve" in path for path in paths))

    def test_activation_and_conversion_forward_to_configured_gateway(self):
        payload = {
            "id": self.project.id,
            "status": "running",
            "active_version": "b" * 32,
            "endpoints": [],
        }
        body = {
            "draft_id": self.draft["id"],
            "expected_revision": self.draft["revision"],
        }
        with (
            patch.dict(os.environ, {"MOCK_HOST": "::", "MOCK_PORT": "4100"}),
            patch("reqlica.cli.load_dotenv"),
            patch(
                "reqlica.console.dependencies.httpx.request",
                return_value=httpx.Response(200, json=payload),
            ) as request,
        ):
            response = self.client.post(self.root + "/activate", json=body)
            self.assertEqual(response.json(), payload)
            request.assert_called_once_with(
                "POST",
                f"http://[::1]:4100{self.root}/activate",
                json=body,
                timeout=60.0,
                trust_env=False,
            )
            self.assertEqual(self.client.post(self.root + "/convert").json(), payload)
            self.assertEqual(
                request.call_args.args[1], f"http://[::1]:4100{self.root}/convert"
            )

    def test_gateway_error_status_and_detail_are_preserved(self):
        for status in (404, 409, 502):
            with (
                self.subTest(status=status),
                patch(
                    "reqlica.console.dependencies.httpx.request",
                    return_value=httpx.Response(
                        status, json={"detail": "activation failed"}
                    ),
                ),
            ):
                response = self.client.post(
                    self.root + "/activate",
                    json={
                        "draft_id": self.draft["id"],
                        "expected_revision": self.draft["revision"],
                    },
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json(), {"detail": "activation failed"})
        with patch(
            "reqlica.console.dependencies.httpx.request",
            side_effect=httpx.ConnectError("refused"),
        ):
            self.assertEqual(self.client.post(self.root + "/convert").status_code, 502)
