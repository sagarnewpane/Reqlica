"""Draft revisions, containment, endpoint edits, and exact-content approval."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from reqlica.projects.drafts import (
    approve_draft,
    create_draft,
    delete_draft_file,
    draft_directory,
    inspect_draft,
    remove_endpoint,
    require_approved,
    upsert_endpoint,
    write_draft_file,
)
from reqlica.projects.store import Endpoint, Manifest, create_project
from reqlica.projects.versions import (
    atomic_write_json,
    code_directory,
    content_digest,
    snapshot_code,
)
from reqlica.validation.checks import test_draft, validate_draft


class DraftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        environment.start()
        self.addCleanup(environment.stop)
        self.project = create_project(self.root / "project")
        self.draft = create_draft(self.project.id)
        self.directory = draft_directory(self.project, self.draft["id"])

    def write(self, path, content):
        self.draft = write_draft_file(
            self.project.id, self.draft["id"], path, content, self.draft["revision"]
        )
        return self.draft

    def check_and_approve(self):
        arguments = (self.project.id, self.draft["id"], self.draft["revision"])
        self.assertEqual(validate_draft(*arguments)["status"], "passed")
        self.assertEqual(test_draft(*arguments)["status"], "passed")
        self.draft = approve_draft(*arguments)

    def test_snapshot_state_and_edits_are_separate_from_live_code(self):
        active = code_directory(self.project)
        revision = content_digest(active)
        self.assertEqual(self.draft["base_version"], self.project.active_version)
        self.assertEqual(self.draft["revision"], revision)
        self.assertEqual(
            self.draft["changes"], {"added": [], "modified": [], "deleted": []}
        )
        state = (
            self.project.directory / ".reqlica" / "drafts" / f"{self.draft['id']}.json"
        )
        self.assertEqual(json.loads(state.read_text())["id"], self.draft["id"])
        self.assertFalse((self.directory / "project.json").exists())
        self.write("support.py", "value = 1\n")
        self.write("endpoints/hello.py", "def hello():\n    return {'draft': True}\n")
        self.draft = delete_draft_file(
            self.project.id,
            self.draft["id"],
            "tests/test_hello.py",
            self.draft["revision"],
        )
        self.assertEqual(
            self.draft["changes"],
            {
                "added": ["support.py"],
                "modified": ["endpoints/hello.py"],
                "deleted": ["tests/test_hello.py"],
            },
        )
        self.assertEqual(content_digest(active), revision)
        self.assertIn("support.py", self.draft["files"])

    def test_cas_and_safe_paths(self):
        old_revision = self.draft["revision"]
        self.write("nested/file.py", "value = 2\n")
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            write_draft_file(
                self.project.id,
                self.draft["id"],
                "nested/file.py",
                "value = 3\n",
                old_revision,
            )
        for path in (
            "../escape.py",
            "/absolute.py",
            "a/../../escape.py",
            "a\\b.py",
            "bad\x00.py",
            ".reqlica/state.json",
            "project.json",
            "versions/a.py",
            "drafts/a.py",
            "data.sqlite",
            "data.sqlite-wal",
            ".git/config",
            "__pycache__/cached.pyc",
            ".",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.write(path, "unsafe")
        self.assertEqual((self.directory / "nested/file.py").read_text(), "value = 2\n")
        self.assertEqual(list(self.directory.rglob("*.tmp")), [])

    def test_symlink_traversal_and_unsafe_ids(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.directory / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.write("link/escaped.py", "no")
        (self.directory / "link").unlink()
        target = self.directory / "linked.py"
        target.symlink_to(outside / "escaped.py")
        with self.assertRaises(ValueError):
            self.write("linked.py", "no")
        target.unlink()
        for draft_id in ("../escape", "", "a" * 31, "A" * 32):
            with self.subTest(draft_id=draft_id), self.assertRaises(ValueError):
                draft_directory(self.project, draft_id)
        self.assertEqual(list(outside.iterdir()), [])

    def test_endpoint_mutations_use_manifest_validation(self):
        endpoint = Endpoint(
            method="GET", path="/other", handler="endpoints/hello.py:hello"
        )
        self.draft = upsert_endpoint(
            self.project.id, self.draft["id"], endpoint, self.draft["revision"]
        )
        self.assertEqual(
            len(
                Manifest.model_validate_json(
                    (self.directory / "manifest.json").read_text()
                ).endpoints
            ),
            2,
        )
        self.draft = upsert_endpoint(
            self.project.id, self.draft["id"], endpoint, self.draft["revision"]
        )
        self.draft = remove_endpoint(
            self.project.id, self.draft["id"], "GET", "/other", self.draft["revision"]
        )
        with self.assertRaisesRegex(ValueError, "not found"):
            remove_endpoint(
                self.project.id,
                self.draft["id"],
                "GET",
                "/other",
                self.draft["revision"],
            )
        first = Endpoint(
            method="GET", path="/items/{id}", handler="endpoints/hello.py:hello"
        )
        self.draft = upsert_endpoint(
            self.project.id, self.draft["id"], first, self.draft["revision"]
        )
        duplicate = Endpoint(
            method="GET", path="/items/{name:str}", handler="endpoints/hello.py:hello"
        )
        with self.assertRaisesRegex(ValueError, "Duplicate route"):
            upsert_endpoint(
                self.project.id, self.draft["id"], duplicate, self.draft["revision"]
            )

    def test_approval_requires_exact_passing_checks_and_explicit_caller(self):
        arguments = (self.project.id, self.draft["id"], self.draft["revision"])
        with self.assertRaisesRegex(ValueError, "must pass"):
            approve_draft(*arguments)
        self.assertEqual(validate_draft(*arguments)["status"], "passed")
        with self.assertRaisesRegex(ValueError, "must pass"):
            approve_draft(*arguments)
        self.assertGreaterEqual(test_draft(*arguments)["tests_run"], 1)
        with self.assertRaisesRegex(ValueError, "Explicit approval"):
            require_approved(*arguments)
        self.draft = approve_draft(*arguments)
        self.assertEqual(
            require_approved(*arguments)["approval"],
            {"revision": self.draft["revision"]},
        )
        self.write("support.py", "value = 1\n")
        for key in ("validation", "test", "approval"):
            self.assertIsNone(self.draft[key])

    def test_external_edit_invalidates_approval_and_revision(self):
        self.check_and_approve()
        old_revision = self.draft["revision"]
        (self.directory / "support.py").write_text("value = 2\n")
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            require_approved(self.project.id, self.draft["id"], old_revision)
        inspected = inspect_draft(self.project.id, self.draft["id"])
        self.assertNotEqual(inspected["revision"], old_revision)
        for key in ("validation", "test", "approval"):
            self.assertIsNone(inspected[key])

    def test_approval_requires_genuinely_successful_test_count(self):
        arguments = (self.project.id, self.draft["id"], self.draft["revision"])
        self.assertEqual(validate_draft(*arguments)["status"], "passed")
        state_path = (
            self.project.directory / ".reqlica" / "drafts" / f"{self.draft['id']}.json"
        )
        state = json.loads(state_path.read_text())
        for tests_passed in (None, 0, 1):
            with self.subTest(tests_passed=tests_passed):
                state["test"] = {
                    "status": "passed",
                    "revision": self.draft["revision"],
                    "tests_run": 3,
                    "tests_passed": 0,
                }
                if tests_passed is None:
                    state["test"].pop("tests_passed")
                else:
                    state["test"]["tests_passed"] = tests_passed
                atomic_write_json(state_path, state)
                if tests_passed:
                    self.assertIsNotNone(approve_draft(*arguments)["approval"])
                else:
                    with self.assertRaisesRegex(ValueError, "passing test"):
                        approve_draft(*arguments)

    def test_active_version_change_blocks_approved_draft(self):
        self.check_and_approve()
        version = uuid4().hex
        snapshot_code(
            code_directory(self.project), self.project.directory / "versions" / version
        )
        metadata = self.project.model_dump(mode="json", exclude={"directory"})
        metadata["active_version"] = version
        atomic_write_json(self.project.directory / "project.json", metadata)
        with self.assertRaisesRegex(ValueError, "base version"):
            require_approved(self.project.id, self.draft["id"], self.draft["revision"])

    def test_legacy_projects_require_explicit_conversion(self):
        metadata = self.project.model_dump(mode="json", exclude={"directory"})
        metadata.pop("active_version")
        atomic_write_json(self.project.directory / "project.json", metadata)
        with self.assertRaisesRegex(ValueError, "versioned"):
            create_draft(self.project.id)

    def test_external_control_file_cannot_change_digest_interpretation(self):
        (self.directory / "project.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "reserved project.json"):
            inspect_draft(self.project.id, self.draft["id"])
