"""Preview URLs run the current draft on disposable data, never published code."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.projects import drafts
from reqlica.projects.store import Endpoint, create_project
from reqlica.projects.urls import get_draft_urls, get_project_urls
from reqlica.projects.versions import code_directory, content_digest


class PreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "project")
        self.draft = drafts.create_draft(self.project.id)
        self.client = self.enterContext(TestClient(create_app()))
        self.base = f"/api/projects/{self.project.id}/drafts/{self.draft['id']}/preview"
        self.active_revision = content_digest(code_directory(self.project))

    def write(self, path, source):
        self.draft = drafts.write_draft_file(
            self.project.id, self.draft["id"], path, source, self.draft["revision"]
        )

    def route(self, method, path, handler):
        self.draft = drafts.upsert_endpoint(
            self.project.id,
            self.draft["id"],
            Endpoint(method=method, path=path, handler=handler),
            self.draft["revision"],
        )

    def test_preview_without_hash_uses_current_code_and_preserves_approval_boundary(
        self,
    ):
        self.write("endpoints/hello.py", "def hello():\n    return {'draft': True}\n")
        response = self.client.get(self.base + "/hello")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"draft": True})
        self.assertEqual(
            response.headers["x-reqlica-draft-revision"], self.draft["revision"]
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.write("endpoints/hello.py", "def hello():\n    return {'draft': 'new'}\n")
        updated = self.client.get(self.base + "/hello")
        self.assertEqual(updated.json(), {"draft": "new"})
        self.assertNotEqual(
            updated.headers["x-reqlica-draft-revision"],
            response.headers["x-reqlica-draft-revision"],
        )
        state = drafts.inspect_draft(self.project.id, self.draft["id"])
        for field in ("validation", "test", "approval"):
            self.assertIsNone(state[field])
        self.assertEqual(
            content_digest(code_directory(self.project)), self.active_revision
        )
        wrong_method = self.client.post(self.base + "/hello", json={})
        self.assertEqual(wrong_method.status_code, 405, wrong_method.text)

    def test_preview_json_requests_and_query_parameters_use_disposable_database_copies(
        self,
    ):
        connection = sqlite3.connect(self.project.directory / "data.sqlite")
        with connection:
            connection.execute(
                "CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)"
            )
            connection.execute("INSERT INTO records(value) VALUES ('baseline')")
        connection.close()
        self.write(
            "endpoints/records.py",
            "from reqlica.runtime.sdk import connection, transaction\n\n"
            "def records(limit: int = 10):\n"
            "    with connection() as db:\n"
            "        return [dict(row) for row in db.execute('SELECT * FROM records LIMIT ?', (limit,))]\n\n"
            "def create(body: dict):\n"
            "    with transaction() as db:\n"
            "        cursor = db.execute('INSERT INTO records(value) VALUES (?)', (body['value'],))\n"
            "        return {'id': cursor.lastrowid, 'value': body['value']}\n",
        )
        self.route("GET", "/records", "endpoints/records.py:records")
        self.route("POST", "/records", "endpoints/records.py:create")
        created = self.client.post(self.base + "/records", json={"value": "discarded"})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json(), {"id": 2, "value": "discarded"})
        self.assertEqual(
            self.client.get(self.base + "/records").json(),
            [{"id": 1, "value": "baseline"}],
        )
        self.assertEqual(self.client.get(self.base + "/records?limit=0").json(), [])
        connection = sqlite3.connect(self.project.directory / "data.sqlite")
        self.assertEqual(
            connection.execute("SELECT value FROM records").fetchall(), [("baseline",)]
        )
        connection.close()

    def test_urls_distinguish_templates_root_and_unpublished_routes(self):
        self.write(
            "endpoints/item.py", "def item(item_id: int):\n    return {'id': item_id}\n"
        )
        self.route("GET", "/items/{item_id}", "endpoints/item.py:item")
        self.route("GET", "/", "endpoints/hello.py:hello")
        draft = get_draft_urls(
            self.project.id, self.draft["id"], "http://127.0.0.1:3000"
        )
        templates = {endpoint["path"]: endpoint for endpoint in draft["endpoints"]}
        self.assertTrue(templates["/items/{item_id}"]["requires_path_parameters"])
        self.assertFalse(templates["/hello"]["requires_path_parameters"])
        self.assertEqual(self.client.get(self.base + "/items/7").json(), {"id": 7})
        self.assertEqual(self.client.get(self.base + "/").status_code, 200)
        published = get_project_urls(self.project.id, "http://127.0.0.1:4000")
        self.assertEqual(
            [endpoint["path"] for endpoint in published["endpoints"]], ["/hello"]
        )
        with self.assertRaises(FileNotFoundError):
            get_project_urls(
                self.project.id, "http://127.0.0.1:4000", "/items/{item_id}"
            )

    def test_bad_bodies_are_rejected_before_execution(self):
        with patch("reqlica.console.routes.preview._call_current") as execute:
            for content, media_type, status in (
                (b"not json", "text/plain", 415),
                (b"{bad", "application/json", 400),
                (b"x" * (1_048_576 + 1), "application/json", 413),
            ):
                with self.subTest(status=status):
                    response = self.client.post(
                        self.base + "/hello",
                        content=content,
                        headers={"Content-Type": media_type},
                    )
                    self.assertEqual(response.status_code, status, response.text[:500])
            execute.assert_not_called()

    def test_failure_statuses_and_revision_conflicts_are_not_hidden(self):
        for status, code in (("failed", 422), ("error", 502), ("timeout", 504)):
            with (
                self.subTest(status=status),
                patch(
                    "reqlica.console.routes.preview._call_current",
                    return_value={
                        "status": status,
                        "output": "check output",
                        "revision": self.draft["revision"],
                    },
                ),
            ):
                response = self.client.get(self.base + "/hello")
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.json()["detail"]["status"], status)
        with patch(
            "reqlica.console.routes.preview.drafts.call_draft_endpoint",
            side_effect=ValueError("Draft revision conflict"),
        ) as call:
            response = self.client.get(self.base + "/hello")
            self.assertEqual(response.status_code, 409)
            self.assertIn("revision conflict", response.json()["detail"])
            call.assert_called_once()
        self.assertEqual(
            self.client.get(
                self.base.replace(self.draft["id"], "f" * 32) + "/hello"
            ).status_code,
            404,
        )

    def test_preview_response_header_filtering_and_empty_responses(self):
        result = {
            "status": "passed",
            "revision": self.draft["revision"],
            "status_code": 201,
            "body": {"saved": True},
            "headers": {
                "content-type": "application/json",
                "content-length": "999",
                "content-encoding": "gzip",
                "connection": "x-hop",
                "x-hop": "remove",
                "set-cookie": "session=ignored",
                "x-custom": "keep",
            },
        }
        with patch("reqlica.console.routes.preview._call_current", return_value=result):
            response = self.client.get(self.base + "/hello")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"saved": True})
        self.assertEqual(int(response.headers["content-length"]), len(response.content))
        self.assertEqual(response.headers["x-custom"], "keep")
        for header in ("content-encoding", "connection", "x-hop", "set-cookie"):
            self.assertNotIn(header, response.headers)
        for method, code in (("GET", 204), ("HEAD", 200)):
            with (
                self.subTest(method=method),
                patch(
                    "reqlica.console.routes.preview._call_current",
                    return_value={**result, "status_code": code},
                ),
            ):
                response = self.client.request(method, self.base + "/hello")
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.content, b"")

    def test_preview_preserves_plain_text_and_json_string_bodies(self):
        result = {
            "status": "passed",
            "revision": self.draft["revision"],
            "status_code": 200,
            "body": "hello",
        }
        for media_type in ("text/plain", "application/json"):
            with (
                self.subTest(media_type=media_type),
                patch(
                    "reqlica.console.routes.preview._call_current",
                    return_value={**result, "headers": {"content-type": media_type}},
                ),
            ):
                response = self.client.get(self.base + "/hello")
                if media_type == "application/json":
                    self.assertEqual(response.json(), "hello")
                else:
                    self.assertEqual(response.text, "hello")
