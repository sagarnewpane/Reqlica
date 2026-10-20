"""Draft identity, metadata, and revision-pinned deletion without live changes."""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.projects import drafts
from reqlica.projects.store import create_project
from reqlica.projects.versions import atomic_write_json, code_directory, content_digest


class DraftManagementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")}))
        self.project = create_project(self.root / "project", "Draft management")
        self.draft = drafts.create_draft(self.project.id, "JWT signup and login")
        self.args = self.project.id, self.draft["id"]
        self.state_path = drafts._state_path(self.project, self.draft["id"])

    def test_creation_inspection_and_summary_keep_stable_identity_and_times(self):
        self.assertEqual(self.draft["name"], "JWT signup and login")
        self.assertIsNotNone(datetime.fromisoformat(self.draft["created_at"]).tzinfo)
        before = self.state_path.stat().st_mtime_ns
        inspected = drafts.inspect_draft(*self.args)
        summary = drafts.list_drafts(self.project.id, summary=True)["drafts"][0]
        for key in ("name", "created_at", "updated_at"):
            self.assertEqual(summary[key], self.draft[key])
            self.assertEqual(inspected[key], self.draft[key])
        self.assertEqual(self.state_path.stat().st_mtime_ns, before)

    def test_rename_preserves_content_checks_approval_and_creation_time(self):
        args = *self.args, self.draft["revision"]
        self.assertEqual(drafts.validate_draft(*args)["status"], "passed")
        self.assertEqual(drafts.test_draft(*args)["status"], "passed")
        before = drafts.approve_draft(*args)
        renamed = drafts.rename_draft(*self.args, "  Reviewed auth  ", self.draft["revision"])
        self.assertEqual(renamed["name"], "Reviewed auth")
        for key in ("revision", "created_at", "validation", "test", "approval", "changes"):
            self.assertEqual(before[key], renamed[key])
        self.assertGreater(renamed["updated_at"], before["updated_at"])
        self.assertEqual(drafts.require_approved(*args)["name"], "Reviewed auth")

    def test_source_edits_update_time_and_reject_stale_rename_or_delete(self):
        updated = drafts.write_draft_file(*self.args, "models/item.py", "value = 1\n", self.draft["revision"])
        self.assertEqual(updated["created_at"], self.draft["created_at"])
        self.assertGreater(updated["updated_at"], self.draft["updated_at"])
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            drafts.rename_draft(*self.args, "Stale", self.draft["revision"])
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            drafts.delete_draft(*self.args, self.draft["revision"], confirm=True)
        self.assertTrue(self.state_path.exists())

    def test_legacy_times_are_not_fabricated_or_written_on_read(self):
        state = json.loads(self.state_path.read_text())
        for key in ("name", "created_at", "updated_at"):
            state.pop(key)
        atomic_write_json(self.state_path, state)
        before = self.state_path.read_bytes()
        legacy = drafts.inspect_draft(*self.args)
        self.assertEqual(legacy["name"], "Draft " + self.draft["id"][:8])
        self.assertIsNone(legacy["created_at"])
        self.assertIsNotNone(datetime.fromisoformat(legacy["updated_at"]).tzinfo)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_deletion_removes_source_metadata_and_schema_only(self):
        other = drafts.create_draft(self.project.id)
        active = content_digest(code_directory(self.project))
        database = (self.project.directory / "data.sqlite").read_bytes()
        schema = self.project.directory / ".reqlica/openapi/drafts" / self.draft["id"]
        schema.mkdir(parents=True)
        (schema / "saved.json").write_text("{}")
        result = drafts.delete_draft(*self.args, self.draft["revision"], confirm=True)
        self.assertEqual(result["status"], "deleted")
        self.assertFalse(self.state_path.exists())
        self.assertFalse(drafts.draft_directory(self.project, self.draft["id"]).exists())
        self.assertFalse(schema.exists())
        self.assertEqual([d["id"] for d in drafts.list_drafts(self.project.id)["drafts"]], [other["id"]])
        self.assertEqual(content_digest(code_directory(self.project)), active)
        self.assertEqual((self.project.directory / "data.sqlite").read_bytes(), database)
        with self.assertRaises(FileNotFoundError):
            drafts.inspect_draft(*self.args)

    def test_delete_requires_confirmation_and_rejects_symlink_artifacts(self):
        for confirm in (False, None, 1, "true"):
            with self.subTest(confirm=confirm), self.assertRaises(ValueError):
                drafts.delete_draft(*self.args, self.draft["revision"], confirm=confirm)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("preserve")
        schema = self.project.directory / ".reqlica/openapi/drafts" / self.draft["id"]
        schema.parent.mkdir(parents=True)
        schema.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            drafts.delete_draft(*self.args, self.draft["revision"], confirm=True)
        self.assertEqual((outside / "keep").read_text(), "preserve")
        self.assertTrue(self.state_path.exists())

    def test_http_name_validation_confirmation_and_missing_draft(self):
        with TestClient(create_app()) as client:
            root = f"/api/projects/{self.project.id}/drafts"
            route = root + "/" + self.draft["id"]
            for name in ("", " ", "x" * 101, "two\nlines"):
                response = client.patch(route, json={"name": name, "expected_revision": self.draft["revision"]})
                self.assertIn(response.status_code, (409, 422))
            self.assertEqual(client.patch(route, json={"name": "Readable title", "expected_revision": self.draft["revision"]}).json()["name"], "Readable title")
            for confirm in (False, 1, "true", None):
                self.assertEqual(client.request("DELETE", route, json={"expected_revision": self.draft["revision"], "confirm": confirm}).status_code, 422)
            self.assertEqual(client.request("DELETE", route, json={"expected_revision": "0" * 64, "confirm": True}).status_code, 409)
            self.assertEqual(client.request("DELETE", route, json={"expected_revision": self.draft["revision"], "confirm": True}).status_code, 200)
            self.assertEqual(client.get(route).status_code, 404)
            self.assertEqual(client.post(root, json={"name": "Manual experiment"}).json()["name"], "Manual experiment")


if __name__ == "__main__":
    unittest.main()
