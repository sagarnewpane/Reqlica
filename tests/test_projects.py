"""Check project scaffolding, registry persistence, and endpoint definitions."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reqlica.projects.store import (
    Endpoint,
    Manifest,
    create_project,
    get_project,
    list_project_ids,
    registry_directory,
)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_create_and_reopen_project(self):
        project = create_project(self.root / "books", name=" Books ")
        self.assertEqual(project.name, "Books")
        self.assertEqual(get_project(project.id), project)
        metadata = json.loads((project.directory / "project.json").read_text())
        self.assertEqual(metadata, {"id": project.id, "name": "Books"})
        manifest = Manifest.model_validate_json(
            (project.directory / "manifest.json").read_text()
        )
        self.assertEqual(
            manifest.endpoints,
            [Endpoint(method="GET", path="/hello", handler="endpoints/hello.py:hello")],
        )
        self.assertTrue((project.directory / "endpoints" / "hello.py").is_file())
        metadata["name"] = "Renamed books"
        (project.directory / "project.json").write_text(json.dumps(metadata))
        reopened = get_project(project.id)
        self.assertEqual(reopened.id, project.id)
        self.assertEqual(reopened.name, "Renamed books")

    def test_list_ids_includes_corrupt_entries_but_ignores_unrelated_files(self):
        self.assertEqual(list_project_ids(), [])
        first = create_project(self.root / "first")
        second = create_project(self.root / "second")
        registry = registry_directory()
        (registry / f"{first.id}.json").write_text("invalid json")
        (registry / "unrelated.json").write_text("{}")
        (registry / f".{second.id}.temporary.tmp").write_text("{}")
        (registry / f"{'f' * 32}.json").mkdir()
        self.assertEqual(list_project_ids(), sorted([first.id, second.id]))

    def test_existing_empty_directory_and_default_name(self):
        directory = self.root / "cafe"
        directory.mkdir()
        project = create_project(directory)
        self.assertEqual(project.name, "cafe")
        self.assertEqual(get_project(project.id).directory, directory.resolve())

    def test_nonempty_directory_is_not_modified(self):
        directory = self.root / "existing"
        directory.mkdir()
        original = directory / "keep.txt"
        original.write_text("untouched")
        with self.assertRaisesRegex(ValueError, "must be empty"):
            create_project(directory)
        self.assertEqual(original.read_text(), "untouched")
        self.assertEqual(list(directory.iterdir()), [original])

    def test_invalid_name_does_not_create_files(self):
        directory = self.root / "invalid"
        with self.assertRaises(ValueError):
            create_project(directory, name=" ")
        self.assertFalse(directory.exists())

    def test_registry_failure_does_not_leave_scaffolding(self):
        registry_root = self.root / "home"
        registry_root.write_text("not a directory")
        directory = self.root / "books"
        with self.assertRaises(OSError):
            create_project(directory)
        self.assertFalse(directory.exists())

    def test_registration_failure_rolls_back_only_created_files(self):
        registry = self.root / "home" / "projects"
        for existing in (False, True):
            with self.subTest(existing=existing):
                previous = set(registry.iterdir()) if registry.exists() else set()
                directory = self.root / str(existing)
                if existing:
                    directory.mkdir()
                with (
                    patch.object(
                        Path, "replace", side_effect=OSError("cannot register")
                    ),
                    self.assertRaisesRegex(OSError, "cannot register"),
                ):
                    create_project(directory)
                self.assertEqual(directory.exists(), existing)
                if existing:
                    self.assertEqual(list(directory.iterdir()), [])
                self.assertEqual(set(registry.iterdir()), previous)
                project = create_project(directory)
                self.assertEqual(get_project(project.id), project)

    def test_missing_and_unsafe_ids(self):
        for project_id in ("a" * 32, "../escape", "demo", ""):
            with (
                self.subTest(project_id=project_id),
                self.assertRaises(FileNotFoundError),
            ):
                get_project(project_id)

    def test_corrupt_registered_project_is_not_reported_as_unknown(self):
        project = create_project(self.root / "corrupt")
        metadata = project.directory / "project.json"
        metadata.write_text(json.dumps({"id": "b" * 32, "name": "wrong ID"}))
        with self.assertRaisesRegex(ValueError, "does not match"):
            get_project(project.id)
        metadata.unlink()
        with self.assertRaisesRegex(ValueError, "Cannot open project"):
            get_project(project.id)


class ManifestTests(unittest.TestCase):
    def test_supported_routes_and_shared_handler(self):
        endpoints = [
            Endpoint(
                method="GET",
                path="/books/{book_id:int}",
                handler="endpoints/books.py:get_book",
            ),
            Endpoint(
                method="POST", path="/books", handler="endpoints/books.py:create_book"
            ),
        ]
        self.assertEqual(Manifest(endpoints=endpoints).endpoints, endpoints)

    def test_rejects_invalid_endpoint_definitions(self):
        cases = [
            {"method": "CONNECT"},
            {"path": "hello"},
            {"path": "/hello?key=value"},
            {"path": "/hello#fragment"},
            {"path": "/hello/{id:unknown}"},
            {"path": "/{id}/{id}"},
            {"handler": "../escape.py:hello"},
            {"handler": "/absolute.py:hello"},
            {"handler": "hello.txt:hello"},
            {"handler": "hello.py"},
            {"handler": "hello.py:hello:other"},
            {"handler": "hello.py:not-a-function"},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                Endpoint.model_validate(
                    {
                        "method": "GET",
                        "path": "/hello",
                        "handler": "hello.py:hello",
                        **case,
                    }
                )

    def test_duplicate_routes_include_equivalent_parameter_names(self):
        for first, second in [
            ("/hello", "/hello"),
            ("/books/{id}", "/books/{name:str}"),
        ]:
            with (
                self.subTest(first=first),
                self.assertRaisesRegex(ValueError, "Duplicate route"),
            ):
                Manifest(
                    endpoints=[
                        Endpoint(method="GET", path=first, handler="hello.py:hello"),
                        Endpoint(method="GET", path=second, handler="hello.py:hello"),
                    ]
                )
