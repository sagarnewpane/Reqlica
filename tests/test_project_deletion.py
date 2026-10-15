"""Project deletion drains workers and removes registry/runtime state conservatively."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.projects.deletion import delete_project_registration
from reqlica.projects.store import create_project, registry_directory
from reqlica.runtime.app import app
from reqlica.runtime.worker import ProjectActivatingError


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "todo", "Todo")
        self.other = create_project(self.root / "books", "Books")
        self.entry = registry_directory() / f"{self.project.id}.json"
        self.client = self.enterContext(TestClient(app))
        self.manager = app.state.worker_manager
        self.route = f"/api/projects/{self.project.id}"

    def delete(self, *, files=False, directory=None):
        return self.client.request(
            "DELETE",
            self.route,
            json={"delete_files": files, "expected_directory": directory},
        )

    def assert_removed(self):
        self.assertFalse(self.entry.exists())
        ids = {
            project["id"]
            for project in self.client.get("/api/projects").json()["projects"]
        }
        self.assertNotIn(self.project.id, ids)
        self.assertIn(self.other.id, ids)
        for state in (
            self.manager.workers,
            self.manager._stopped,
            self.manager._errors,
            self.manager._requests,
        ):
            self.assertNotIn(self.project.id, state)
        self.assertNotIn(self.project.id, self.manager._activating)

    def test_unregister_stops_running_worker_and_preserves_files_and_other_workers(
        self,
    ):
        self.assertEqual(
            self.client.get(f"/mock/{self.project.id}/hello").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/mock/{self.other.id}/hello").status_code, 200
        )
        worker = self.manager.workers[self.project.id]
        other_pid = self.manager.workers[self.other.id].process.pid
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["files_deleted"])
        self.assertTrue(worker.closed)
        self.assertTrue((self.project.directory / "data.sqlite").exists())
        self.assertTrue((self.project.directory / "project.json").exists())
        self.assertEqual(self.manager.workers[self.other.id].process.pid, other_pid)
        self.assert_removed()
        self.assertEqual(
            self.client.get(f"/mock/{self.project.id}/hello").status_code, 404
        )

    def test_explicit_file_deletion_removes_folder_and_all_caches(self):
        self.manager.stop(self.project.id)
        self.manager._errors[self.project.id] = "old runtime error"
        info = self.client.get(self.route + "/deletion", params={"delete_files": True})
        self.assertEqual(info.status_code, 200, info.text)
        response = self.delete(files=True, directory=info.json()["directory"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["files_deleted"])
        self.assertFalse(self.project.directory.exists())
        self.assertTrue(self.other.directory.exists())
        self.assert_removed()

    def test_stale_invalid_and_already_unregistered_entries_can_be_cleaned(self):
        self.entry.write_text("not JSON")
        info = self.client.get(self.route + "/deletion").json()
        self.assertTrue(info["registered"])
        self.assertTrue(info["error"])
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.project.directory.exists())
        self.assert_removed()
        self.assertEqual(self.delete().status_code, 404)

    def test_cached_stopped_project_without_registry_is_forgotten(self):
        self.manager.stop(self.project.id)
        self.entry.unlink()
        info = self.client.get(self.route + "/deletion")
        self.assertEqual(info.status_code, 200)
        self.assertFalse(info.json()["registered"])
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["unregistered"])
        self.assert_removed()

    def test_directory_must_match_confirmation_and_identity(self):
        for directory in (None, str(self.other.directory)):
            with self.subTest(directory=directory):
                response = self.delete(files=True, directory=directory)
                self.assertEqual(response.status_code, 409)
                self.assertTrue(self.project.directory.exists())
        metadata = self.project.directory / "project.json"
        content = json.loads(metadata.read_text())
        metadata.write_text(json.dumps({**content, "id": self.other.id}))
        response = self.delete(files=True, directory=str(self.project.directory))
        self.assertEqual(response.status_code, 409)
        self.assertIn("ID does not match", response.json()["detail"])
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(metadata.exists())

    def test_shared_nested_and_protected_directories_cannot_be_erased(self):
        alias = registry_directory() / ("f" * 32 + ".json")
        alias.write_text(self.entry.read_text())
        response = self.delete(files=True, directory=str(self.project.directory))
        self.assertEqual(response.status_code, 409)
        self.assertIn("overlaps", response.json()["detail"])
        alias.unlink()
        nested = create_project(self.project.directory / "nested", "Nested")
        response = self.delete(files=True, directory=str(self.project.directory))
        self.assertEqual(response.status_code, 409)
        self.assertTrue(nested.directory.exists())
        (registry_directory() / f"{nested.id}.json").unlink()
        # A bad registration cannot authorize deleting an ancestor of the registry.
        (self.root / "project.json").write_text(
            (self.project.directory / "project.json").read_text()
        )
        self.entry.write_text(json.dumps({"directory": str(self.root)}))
        response = self.delete(files=True, directory=str(self.root))
        self.assertEqual(response.status_code, 409)
        self.assertIn("protected", response.json()["detail"])
        self.assertTrue(self.other.directory.exists())

    def test_symlinks_do_not_authorize_deleting_targets(self):
        link = self.root / "alias"
        link.symlink_to(self.project.directory, target_is_directory=True)
        self.entry.write_text(json.dumps({"directory": str(link)}))
        response = self.delete(files=True, directory=str(link))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(self.project.directory.exists())
        self.assertTrue(link.is_symlink())

    def test_symlink_children_are_not_followed_during_file_deletion(self):
        sentinel = self.other.directory / "keep.txt"
        sentinel.write_text("keep")
        (self.project.directory / "link").symlink_to(
            self.other.directory, target_is_directory=True
        )
        response = self.delete(files=True, directory=str(self.project.directory))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_registry_symlink_is_unlinked_without_removing_its_target(self):
        other_entry = registry_directory() / f"{self.other.id}.json"
        self.entry.unlink()
        self.entry.symlink_to(other_entry)
        response = self.delete(files=True, directory=str(self.other.directory))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(other_entry.exists())
        self.assertTrue(self.other.directory.exists())
        self.assertTrue(self.project.directory.exists())

    def test_symlink_metadata_cannot_authorize_file_deletion(self):
        metadata = self.project.directory / "project.json"
        original = self.root / "metadata.json"
        metadata.rename(original)
        metadata.symlink_to(original)
        response = self.delete(files=True, directory=str(self.project.directory))
        self.assertEqual(response.status_code, 409)
        self.assertTrue(original.exists())
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(self.project.directory.exists())

    def test_file_cleanup_failure_is_reported_and_project_stays_stopped(self):
        with patch(
            "reqlica.projects.deletion.shutil.rmtree",
            side_effect=PermissionError("denied"),
        ):
            response = self.delete(files=True, directory=str(self.project.directory))
        self.assertEqual(response.status_code, 502)
        self.assertTrue(self.entry.exists())
        self.assertTrue(self.project.directory.exists())
        self.assertIn(self.project.id, self.manager._stopped)
        self.assertEqual(
            self.client.get(f"/mock/{self.project.id}/hello").status_code, 503
        )
        self.assertNotIn(self.project.id, self.manager._activating)
        self.assertEqual(self.delete().status_code, 200)
        self.assert_removed()

    def test_in_flight_requests_are_drained_without_deleting_on_timeout(self):
        self.manager._requests[self.project.id] = 1
        try:
            with patch("reqlica.runtime.deletion.DRAIN_TIMEOUT", 0.01):
                response = self.delete()
            self.assertEqual(response.status_code, 502)
            self.assertIn("draining", response.json()["detail"])
            self.assertTrue(self.entry.exists())
            self.assertNotIn(self.project.id, self.manager._stopped)
        finally:
            self.manager._requests.pop(self.project.id, None)

    def test_new_requests_are_blocked_until_deletion_finishes(self):
        entered, release = Event(), Event()

        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test timed out")
            return delete_project_registration(*args, **kwargs)

        with (
            patch(
                "reqlica.runtime.deletion.delete_project_registration",
                side_effect=blocked,
            ),
            ThreadPoolExecutor() as pool,
        ):
            future = pool.submit(self.manager.delete, self.project.id)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(ProjectActivatingError):
                    self.manager.acquire_request(self.project.id)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)["status"], "deleted")
        self.assert_removed()

    def test_concurrent_external_unregistration_does_not_leave_stopped_cache(self):
        def unregister_first(*args, **kwargs):
            self.entry.unlink()
            return delete_project_registration(*args, **kwargs)

        with patch(
            "reqlica.runtime.deletion.delete_project_registration",
            side_effect=unregister_first,
        ):
            response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["unregistered"])
        self.assertTrue(self.project.directory.exists())
        self.assert_removed()
