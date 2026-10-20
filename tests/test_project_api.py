"""HTTP project creation reuses safe scaffolding and the existing deletion API."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.projects.store import get_project, list_project_ids
from reqlica.projects.versions import code_directory
from reqlica.runtime.app import app


class ProjectAPITests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")})
        )
        self.client = self.enterContext(TestClient(app))

    def create(self, directory=None, **kwargs):
        return self.client.post(
            "/api/projects",
            json={"directory": str(directory or self.root / "books"), **kwargs},
        )

    def test_create_returns_discoverable_idle_project_with_urls(self):
        response = self.create(name=" Books ")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["name"], "Books")
        self.assertEqual(body["directory"], str(self.root / "books"))
        self.assertEqual(body["status"], "idle")
        self.assertIsNone(body["pid"])
        self.assertIsNone(body["error"])
        self.assertIsNotNone(body["active_version"])
        self.assertEqual(body["url"], f"http://testserver/mock/{body['id']}")
        self.assertEqual(body["endpoints"][0]["url"], body["url"] + "/hello")
        project = get_project(body["id"])
        self.assertTrue((code_directory(project) / "tests" / "test_hello.py").exists())
        self.assertTrue((project.directory / "data.sqlite").exists())
        self.assertEqual(app.state.worker_manager.workers, {})
        self.assertEqual(self.client.get("/api/projects").json(), {"projects": [body]})
        self.assertEqual(
            self.client.get(f"/api/projects/{project.id}/endpoints").json(), body
        )

    def test_accepts_empty_directory_and_defaults_name(self):
        directory = self.root / "default-name"
        directory.mkdir()
        response = self.create(directory)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["name"], "default-name")

    def test_tilde_and_canonical_paths(self):
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            response = self.create("~/books")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["directory"], str(self.root / "books"))

    def test_invalid_bodies_have_no_filesystem_effects(self):
        for payload in (
            {},
            {"directory": ""},
            {"directory": "   "},
            {"directory": "."},
            {"directory": "relative/books"},
            {"directory": "/books\x00"},
            {"directory": str(self.root / "books"), "name": "   "},
            {"directory": str(self.root / "books"), "name": ""},
            {"directory": str(self.root / "books"), "delete_files": True},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/api/projects", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(list_project_ids(), [])
                self.assertFalse((self.root / "books").exists())

    def test_existing_files_are_never_overwritten(self):
        directory = self.root / "books"
        directory.mkdir()
        sentinel = directory / "keep.txt"
        sentinel.write_text("keep")
        response = self.create(directory, name="Books")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(list(directory.iterdir()), [sentinel])
        self.assertEqual(list_project_ids(), [])
        response = self.create(sentinel)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_repeat_creation_is_conflict_not_overwrite(self):
        first = self.create().json()
        metadata = (self.root / "books" / "project.json").read_bytes()
        response = self.create(name="Different")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(list_project_ids(), [first["id"]])
        self.assertEqual((self.root / "books" / "project.json").read_bytes(), metadata)

    def test_concurrent_creation_registers_only_one_complete_project(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.create(), range(2)))
        self.assertEqual(
            sorted(response.status_code for response in responses), [201, 409]
        )
        self.assertEqual(len(list_project_ids()), 1)
        project = get_project(list_project_ids()[0])
        self.assertTrue((project.directory / "data.sqlite").exists())
        self.assertTrue((code_directory(project) / "manifest.json").exists())

    def test_permissions_and_io_failures_are_distinct(self):
        for error, status in (
            (PermissionError("denied"), 403),
            (OSError("disk full"), 500),
        ):
            with (
                self.subTest(status=status),
                patch("reqlica.runtime.app.create_project_files", side_effect=error),
            ):
                response = self.create()
                self.assertEqual(response.status_code, status)
                self.assertIn(str(error), response.json()["detail"])

    def test_registration_failure_rolls_back_scaffolding(self):
        with patch.object(Path, "replace", side_effect=OSError("cannot register")):
            response = self.create()
        self.assertEqual(response.status_code, 500)
        self.assertFalse((self.root / "books").exists())
        self.assertEqual(list_project_ids(), [])

    def test_created_project_can_be_unregistered_without_file_deletion(self):
        body = self.create().json()
        response = self.client.request(
            "DELETE", f"/api/projects/{body['id']}", json={"delete_files": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["files_deleted"])
        self.assertTrue((Path(body["directory"]) / "project.json").exists())
        self.assertEqual(self.client.get("/api/projects").json(), {"projects": []})

    def test_permanent_deletion_requires_confirmed_directory(self):
        body = self.create().json()
        route = f"/api/projects/{body['id']}"
        info = self.client.get(route + "/deletion?delete_files=true").json()
        rejected = self.client.request("DELETE", route, json={"delete_files": True})
        self.assertEqual(rejected.status_code, 409)
        self.assertTrue(Path(body["directory"]).exists())
        response = self.client.request(
            "DELETE",
            route,
            json={"delete_files": True, "expected_directory": info["directory"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["files_deleted"])
        self.assertFalse(Path(body["directory"]).exists())
        self.assertEqual(list_project_ids(), [])

    def test_openapi_documents_creation_body_and_created_status(self):
        operation = self.client.get("/openapi.json").json()["paths"]["/api/projects"][
            "post"
        ]
        self.assertIn("201", operation["responses"])
        self.assertIn("requestBody", operation)
