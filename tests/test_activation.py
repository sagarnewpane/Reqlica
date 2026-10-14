"""Publish approved drafts against real spawn workers and recover interrupted writes."""

import fcntl
import json
import multiprocessing
import os
import signal
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from multiprocessing.connection import Connection
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from reqlica.projects.drafts import (
    approve_draft,
    create_draft,
    draft_directory,
    inspect_draft,
    write_draft_file,
)
from reqlica.projects.store import create_project, get_project
from reqlica.projects.versions import (
    atomic_write_json,
    code_directory,
    content_digest,
    snapshot_code,
)
from reqlica.runtime.activation import recover_activation
from reqlica.runtime.app import app
from reqlica.runtime.sdk import backup_database, restore_database
from reqlica.runtime.worker import (
    ProjectActivatingError,
    ProjectStoppedError,
    WorkerManager,
    _run_worker,
)
from reqlica.validation.checks import test_draft, validate_draft


def _dedicated_gateway(project_id, channel):
    """Give the orphan-worker test a killable parent, not the unittest process."""
    os.setsid()
    manager = WorkerManager()
    try:
        worker = manager.get_or_start(project_id)
        channel.send((worker.process.pid, worker.port))
        channel.recv()
    finally:
        manager.close()
        channel.close()


def _paused_run_worker(directory, project_id, listener, sender, database, parent_pid):
    """Pause before even acquiring runtime.lock, not inside candidate imports."""
    state = database.parent / ".reqlica"
    (state / "candidate-paused").touch()
    deadline = time.monotonic() + 30
    while not (state / "candidate-go").exists():
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)
    _run_worker(directory, project_id, listener, sender, database, parent_pid)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )

    def project(self, name="project"):
        project = create_project(self.root / name)
        self.assertIsNotNone(project.active_version)
        with closing(sqlite3.connect(project.directory / "data.sqlite")) as database:
            database.execute(
                "CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)"
            )
            database.execute("INSERT INTO records VALUES (1, 'keep me')")
            database.commit()
        return project

    def manager(self):
        manager = WorkerManager()
        self.addCleanup(manager.close)
        self.assertEqual(manager._process_context.get_start_method(), "spawn")
        return manager

    def default_response(self, project):
        return {"message": "Hello from Reqlica", "project_id": project.id}

    def draft(self, project, response=None, import_source=""):
        draft = create_draft(project.id)
        if response is not None or import_source:
            if response is None:
                response = self.default_response(project)
            source = import_source + f"\ndef hello():\n    return {response!r}\n"
            draft = write_draft_file(
                project.id, draft["id"], "endpoints/hello.py", source, draft["revision"]
            )
            source = (
                "import unittest\n"
                "from endpoints.hello import hello\n\n"
                "class HelloTests(unittest.TestCase):\n"
                "    def test_hello(self):\n"
                f"        self.assertEqual(hello(), {response!r})\n"
            )
            draft = write_draft_file(
                project.id,
                draft["id"],
                "tests/test_hello.py",
                source,
                draft["revision"],
            )
        return draft

    def check(self, project, draft):
        arguments = (project.id, draft["id"], draft["revision"])
        validation = validate_draft(*arguments)
        self.assertEqual(validation["status"], "passed", validation)
        self.assertEqual(validation["revision"], draft["revision"])
        tests = test_draft(*arguments)
        self.assertEqual(tests["status"], "passed", tests)
        self.assertEqual(tests["revision"], draft["revision"])
        self.assertGreaterEqual(tests["tests_run"], 1)
        return inspect_draft(project.id, draft["id"])

    def approve(self, project, draft):
        self.check(project, draft)
        approved = approve_draft(project.id, draft["id"], draft["revision"])
        self.assertEqual(approved["approval"], {"revision": draft["revision"]})
        return approved

    def payload(self, draft):
        return {"draft_id": draft["id"], "expected_revision": draft["revision"]}

    def database_state(self, project):
        with closing(sqlite3.connect(project.directory / "data.sqlite")) as database:
            self.assertEqual(
                database.execute("PRAGMA integrity_check").fetchone(), ("ok",)
            )
            return list(database.iterdump())

    def live_write_source(self, project, marker, extra=""):
        return (
            "from pathlib import Path\n"
            "from reqlica.runtime.sdk import database_path, transaction\n"
            f"if database_path() == Path({str(project.directory / 'data.sqlite')!r}):\n"
            "    with transaction() as database:\n"
            "        database.execute(\"UPDATE records SET value = 'candidate write' WHERE id = 1\")\n"
            "        database.execute(\"INSERT INTO records VALUES (2, 'candidate only')\")\n"
            f"    Path({str(marker)!r}).write_text('live import ran')\n" + extra
        )

    def assert_clean(self, project):
        state = project.directory / ".reqlica"
        self.assertFalse((state / "activation.json").exists())
        self.assertFalse((state / "activation-backup.sqlite").exists())

    def assert_hello(self, worker, expected):
        self.assertTrue(worker.process.is_alive())
        self.assertNotEqual(worker.process.pid, os.getpid())
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f"http://127.0.0.1:{worker.port}/hello")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)

    def wait_activating(self, manager, project_id, future):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with manager._lock:
                if project_id in manager._activating:
                    return
            if future.done():
                future.result()
                self.fail("Activation finished before draining its leased request")
            time.sleep(0.01)
        self.fail("Activation did not enter the draining state")

    def inject_journal(self, project, phase):
        """Simulate a crash on disk; ordinary drafts still use the editing API."""
        response = {"message": "candidate", "project_id": project.id}
        draft = self.draft(project, response)
        version = uuid4().hex
        revision = snapshot_code(
            draft_directory(project, draft["id"]),
            project.directory / "versions" / version,
        )
        candidate = project.model_copy(update={"active_version": version})
        state = project.directory / ".reqlica"
        if phase != "prepared":
            backup_database(
                project.directory / "data.sqlite", state / "activation-backup.sqlite"
            )
        if phase in {"backed_up", "committed"}:
            with closing(
                sqlite3.connect(project.directory / "data.sqlite")
            ) as database:
                database.execute(
                    "UPDATE records SET value = 'candidate write' WHERE id = 1"
                )
                database.execute("INSERT INTO records VALUES (2, 'candidate only')")
                database.commit()
        pointer = project if phase == "recovered" else candidate
        atomic_write_json(
            project.directory / "project.json",
            pointer.model_dump(mode="json", exclude={"directory"}),
        )
        record = {
            "phase": phase,
            "previous": project.model_dump(mode="json", exclude={"directory"}),
            "candidate": version,
            "revision": revision,
        }
        atomic_write_json(state / "activation.json", record)
        return candidate, response

    def test_manager_activation_replaces_running_code_and_preserves_wal_data(self):
        project = self.project()
        old_code = content_digest(code_directory(project))
        expected = {"message": "published", "project_id": project.id}
        draft = self.approve(project, self.draft(project, expected))
        manager = self.manager()
        old = manager.get_or_start(project.id)
        old_pid = old.process.pid
        self.assert_hello(old, self.default_response(project))

        # Keep a committed write in an open WAL connection across the backup.
        with closing(sqlite3.connect(project.directory / "data.sqlite")) as database:
            self.assertEqual(
                database.execute("PRAGMA journal_mode = WAL").fetchone(), ("wal",)
            )
            database.execute("INSERT INTO records VALUES (2, 'committed WAL data')")
            database.commit()
            self.assertTrue((project.directory / "data.sqlite-wal").exists())
            before = self.database_state(project)
            result = manager.activate(project.id, draft["id"], draft["revision"])
            self.assertEqual(self.database_state(project), before)
            self.assertEqual(
                database.execute("SELECT value FROM records ORDER BY id").fetchall(),
                [("keep me",), ("committed WAL data",)],
            )

        current = get_project(project.id)
        self.assertNotEqual(current.active_version, project.active_version)
        self.assertEqual(result["active_version"], current.active_version)
        self.assertEqual(result["status"], "running")
        self.assertIsNone(result["error"])
        self.assertNotEqual(result["pid"], old_pid)
        self.assertEqual(content_digest(code_directory(current)), draft["revision"])
        self.assertEqual(content_digest(code_directory(project)), old_code)
        self.assertEqual(manager.workers[project.id].project, current)
        self.assert_hello(manager.get_or_start(project.id), expected)
        self.assertNotIn(
            old_pid, {child.pid for child in multiprocessing.active_children()}
        )
        self.assert_clean(project)

    def test_activation_api_publishes_approved_default_and_reports_urls(self):
        project = self.project()
        draft = self.approve(project, self.draft(project))
        before = self.database_state(project)
        with TestClient(app, base_url="http://gateway.example:4100") as client:
            url = f"/mock/{project.id}/hello"
            self.assertEqual(client.get(url).json(), self.default_response(project))
            manager = app.state.worker_manager
            old_pid = manager.workers[project.id].process.pid
            response = client.post(
                f"/api/projects/{project.id}/activate", json=self.payload(draft)
            )
            self.assertEqual(response.status_code, 200, response.text)
            info = response.json()
            self.assertEqual(info["id"], project.id)
            self.assertEqual(info["status"], "running")
            self.assertNotEqual(info["pid"], old_pid)
            self.assertNotEqual(info["active_version"], project.active_version)
            self.assertEqual(
                info["active_version"], get_project(project.id).active_version
            )
            self.assertEqual(
                info["url"], f"http://gateway.example:4100/mock/{project.id}"
            )
            self.assertEqual(info["endpoints"][0]["url"], info["url"] + "/hello")
            self.assertEqual(client.get(url).json(), self.default_response(project))
            self.assertEqual(
                client.get(f"/api/projects/{project.id}/endpoints").json(), info
            )
            self.assertEqual(self.database_state(project), before)
            self.assert_clean(project)
        self.assertEqual(manager.workers, {})

    def test_idle_and_explicitly_stopped_activation_preserve_lifecycle_state(self):
        for status in ("idle", "stopped"):
            with self.subTest(status=status):
                project = self.project(status)
                expected = {"state": status}
                draft = self.approve(project, self.draft(project, expected))
                before = self.database_state(project)
                manager = self.manager()
                if status == "stopped":
                    manager.get_or_start(project.id)
                    manager.stop(project.id)
                self.assertEqual(manager.describe_project(project.id)["status"], status)
                result = manager.activate(project.id, draft["id"], draft["revision"])
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["pid"])
                self.assertNotEqual(result["active_version"], project.active_version)
                self.assertEqual(manager.describe_project(project.id), result)
                self.assertNotIn(project.id, manager.workers)
                self.assertEqual(self.database_state(project), before)
                self.assert_clean(project)
                if status == "stopped":
                    with self.assertRaises(ProjectStoppedError):
                        manager.acquire_request(project.id)
                    self.assertEqual(manager.stop(project.id), result)
                    self.assertEqual(manager.start(project.id)["status"], "running")
                self.assert_hello(manager.get_or_start(project.id), expected)

    def test_unchecked_and_unapproved_drafts_are_conflicts_without_disrupting_worker(
        self,
    ):
        project = self.project()
        draft = self.draft(project)
        before = self.database_state(project)
        with TestClient(app) as client:
            manager = app.state.worker_manager
            old = manager.get_or_start(project.id)
            for stage in ("unchecked", "validation only", "checks without approval"):
                with self.subTest(stage=stage):
                    if stage == "validation only":
                        result = validate_draft(
                            project.id, draft["id"], draft["revision"]
                        )
                        self.assertEqual(result["status"], "passed", result)
                    elif stage == "checks without approval":
                        self.check(project, draft)
                    response = client.post(
                        f"/api/projects/{project.id}/activate", json=self.payload(draft)
                    )
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn(
                        "Explicit approval"
                        if stage == "checks without approval"
                        else "must pass",
                        response.json()["detail"],
                    )
                    self.assertIs(manager.get_or_start(project.id), old)
                    self.assertEqual(get_project(project.id), project)
                    self.assertEqual(self.database_state(project), before)
                    self.assertNotIn(project.id, manager._activating)
                    self.assert_clean(project)
            approved = approve_draft(project.id, draft["id"], draft["revision"])
            self.assertEqual(
                client.post(
                    f"/api/projects/{project.id}/activate", json=self.payload(approved)
                ).status_code,
                200,
            )

    def test_edit_invalidates_approval_and_old_revision_cannot_activate(self):
        project = self.project()
        draft = self.approve(project, self.draft(project))
        old_revision = draft["revision"]
        draft = write_draft_file(
            project.id, draft["id"], "support.py", "VALUE = 1\n", old_revision
        )
        self.assertNotEqual(draft["revision"], old_revision)
        for key in ("validation", "test", "approval"):
            self.assertIsNone(draft[key])
        manager = self.manager()
        old = manager.get_or_start(project.id)
        before = self.database_state(project)
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            manager.activate(project.id, draft["id"], old_revision)
        with self.assertRaisesRegex(ValueError, "must pass"):
            manager.activate(project.id, draft["id"], draft["revision"])
        self.assertEqual(get_project(project.id), project)
        self.assertEqual(self.database_state(project), before)
        self.assertIs(manager.get_or_start(project.id), old)
        self.assert_clean(project)

    def test_approved_draft_with_stale_base_version_is_rejected(self):
        project = self.project()
        first = self.approve(project, self.draft(project))
        stale = self.approve(project, self.draft(project, {"stale": True}))
        manager = self.manager()
        manager.get_or_start(project.id)
        published = manager.activate(project.id, first["id"], first["revision"])
        old = manager.get_or_start(project.id)
        before = self.database_state(project)
        with self.assertRaisesRegex(ValueError, "base version"):
            manager.activate(project.id, stale["id"], stale["revision"])
        self.assertEqual(
            get_project(project.id).active_version, published["active_version"]
        )
        self.assertIs(manager.get_or_start(project.id), old)
        self.assertEqual(self.database_state(project), before)
        self.assert_clean(project)

    def test_injected_check_and_approval_records_must_match_exact_revision(self):
        project = self.project()
        draft = self.approve(project, self.draft(project))
        state_path = project.directory / ".reqlica" / "drafts" / f"{draft['id']}.json"
        original = json.loads(state_path.read_text())
        manager = self.manager()
        old = manager.get_or_start(project.id)
        before = self.database_state(project)
        corruptions = (
            ("validation", "revision", "0" * 64),
            ("validation", "status", "failed"),
            ("test", "revision", "0" * 64),
            ("test", "status", "failed"),
            ("test", "tests_run", 0),
            ("test", "tests_passed", 0),
            ("approval", "revision", "0" * 64),
        )
        for record, key, value in corruptions:
            with self.subTest(record=record, key=key):
                # Intentional state corruption, not a shortcut around real checks.
                injected = json.loads(json.dumps(original))
                injected[record][key] = value
                atomic_write_json(state_path, injected)
                with self.assertRaises(ValueError):
                    manager.activate(project.id, draft["id"], draft["revision"])
                self.assertEqual(get_project(project.id), project)
                self.assertIs(manager.get_or_start(project.id), old)
                self.assertEqual(self.database_state(project), before)
                self.assertNotIn(project.id, manager._activating)
                self.assert_clean(project)
        atomic_write_json(state_path, original)

    def test_idle_and_stopped_failed_imports_restore_pointer_data_and_status(self):
        for status in ("idle", "stopped"):
            for failure in ("import", "schema"):
                with self.subTest(status=status, failure=failure):
                    project = self.project(f"{status}-{failure}")
                    marker = self.root / f"{status}-{failure}-ran"
                    if failure == "import":
                        extra = "    raise RuntimeError('live import failed after commit')\n"
                        exception, message = (
                            RuntimeError,
                            "live import failed after commit",
                        )
                    else:
                        extra = (
                            "    with transaction() as database:\n"
                            "        database.execute('CREATE TABLE candidate_table (value TEXT)')\n"
                        )
                        exception, message = ValueError, "Schema-changing"
                    draft = self.approve(
                        project,
                        self.draft(
                            project,
                            {"candidate": True},
                            self.live_write_source(project, marker, extra),
                        ),
                    )
                    self.assertFalse(marker.exists())
                    manager = self.manager()
                    if status == "stopped":
                        manager.get_or_start(project.id)
                        manager.stop(project.id)
                    before = self.database_state(project)
                    pointer = (project.directory / "project.json").read_bytes()
                    old_code = content_digest(code_directory(project))
                    children = {
                        child.pid for child in multiprocessing.active_children()
                    }
                    with self.assertRaisesRegex(exception, message):
                        manager.activate(project.id, draft["id"], draft["revision"])
                    self.assertEqual(marker.read_text(), "live import ran")
                    self.assertEqual(get_project(project.id), project)
                    self.assertEqual(
                        (project.directory / "project.json").read_bytes(), pointer
                    )
                    self.assertEqual(content_digest(code_directory(project)), old_code)
                    self.assertEqual(self.database_state(project), before)
                    result = manager.describe_project(project.id)
                    self.assertEqual(result["status"], status)
                    self.assertIsNone(result["pid"])
                    self.assertIsNone(result["error"])
                    self.assertEqual(manager.workers, {})
                    self.assertEqual(
                        {child.pid for child in multiprocessing.active_children()},
                        children,
                    )
                    self.assertNotIn(project.id, manager._activating)
                    if status == "stopped":
                        with self.assertRaises(ProjectStoppedError):
                            manager.acquire_request(project.id)
                    self.assert_clean(project)

    def test_prepared_journal_replace_then_fsync_failure_retains_worker_and_allows_retry(
        self,
    ):
        project = self.project()
        marker = self.root / "prepared-candidate-ran"
        expected = {"retry": True}
        draft = self.approve(
            project,
            self.draft(project, expected, self.live_write_source(project, marker)),
        )
        manager = self.manager()
        old = manager.get_or_start(project.id)
        before = self.database_state(project)
        pointer = (project.directory / "project.json").read_bytes()
        journal = project.directory / ".reqlica" / "activation.json"
        failed = False

        def fail_after_prepared_replace(path, record):
            nonlocal failed
            atomic_write_json(path, record)
            if path == journal and record["phase"] == "prepared" and not failed:
                failed = True
                self.assertEqual(json.loads(journal.read_text()), record)
                raise OSError("prepared directory fsync failed after replace")

        with (
            patch(
                "reqlica.runtime.activation.atomic_write_json",
                side_effect=fail_after_prepared_replace,
            ),
            self.assertRaisesRegex(OSError, "fsync failed after replace"),
        ):
            manager.activate(project.id, draft["id"], draft["revision"])
        self.assertTrue(failed)
        self.assertIs(manager.workers[project.id], old)
        self.assert_hello(old, self.default_response(project))
        self.assertFalse(marker.exists())
        self.assertEqual(get_project(project.id), project)
        self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        self.assertEqual(self.database_state(project), before)
        self.assertNotIn(project.id, manager._activating)
        self.assert_clean(project)
        result = manager.activate(project.id, draft["id"], draft["revision"])
        self.assertEqual(result["status"], "running")
        self.assertNotEqual(result["active_version"], project.active_version)
        self.assertEqual(marker.read_text(), "live import ran")
        self.assert_hello(manager.workers[project.id], expected)
        self.assert_clean(project)

    def test_old_stop_failure_retains_live_worker_recovers_journal_and_allows_retry(
        self,
    ):
        project = self.project()
        marker = self.root / "old-stop-candidate-ran"
        expected = {"retry": True}
        draft = self.approve(
            project,
            self.draft(project, expected, self.live_write_source(project, marker)),
        )
        manager = self.manager()
        old = manager.get_or_start(project.id)
        before = self.database_state(project)
        pointer = (project.directory / "project.json").read_bytes()
        stop_worker = manager._stop_worker

        def fail_old_stop(worker):
            if worker is old and worker.process.is_alive():
                raise OSError("old worker stop failed")
            return stop_worker(worker)

        with (
            patch.object(manager, "_stop_worker", side_effect=fail_old_stop),
            self.assertRaisesRegex(OSError, "old worker stop failed"),
        ):
            manager.activate(project.id, draft["id"], draft["revision"])
        self.assertIs(manager.workers[project.id], old)
        self.assert_hello(old, self.default_response(project))
        self.assertFalse(marker.exists())
        self.assertEqual(get_project(project.id), project)
        self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        self.assertEqual(self.database_state(project), before)
        self.assertNotIn(project.id, manager._activating)
        self.assert_clean(project)
        result = manager.activate(project.id, draft["id"], draft["revision"])
        self.assertEqual(result["status"], "running")
        self.assertNotEqual(result["active_version"], project.active_version)
        self.assertEqual(marker.read_text(), "live import ran")
        self.assert_hello(manager.workers[project.id], expected)
        self.assert_clean(project)

    def test_candidate_cleanup_failure_retains_ownership_until_close_and_startup_recovery(
        self,
    ):
        project = self.project()
        marker = self.root / "unreleased-candidate-ran"
        draft = self.approve(
            project,
            self.draft(
                project, {"candidate": True}, self.live_write_source(project, marker)
            ),
        )
        manager = self.manager()
        before = self.database_state(project)
        pointer = (project.directory / "project.json").read_bytes()
        old_code = content_digest(code_directory(project))
        candidates = []
        start_worker = manager._start_worker
        stop_worker = manager._stop_worker

        def capture_candidate(candidate_project):
            worker = start_worker(candidate_project)
            candidates.append(worker)
            return worker

        def cleanup_candidates():
            manager.close()
            # Bound process cleanup even if a regression loses manager ownership.
            for worker in candidates:
                if not worker.closed:
                    stop_worker(worker)

        self.addCleanup(cleanup_candidates)

        def fail_candidate_stop(worker):
            if any(worker is candidate for candidate in candidates):
                raise OSError("candidate worker stop failed")
            return stop_worker(worker)

        with (
            patch.object(manager, "_start_worker", side_effect=capture_candidate),
            patch.object(
                manager, "_stop_worker", side_effect=fail_candidate_stop
            ) as stop,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "recovery failed.*candidate worker stop failed"
            ):
                manager.activate(project.id, draft["id"], draft["revision"])
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            candidate_pid = candidate.process.pid
            self.assertEqual(stop.call_count, 2)
            self.assertIs(manager.workers[project.id], candidate)
            self.assertTrue(candidate.process.is_alive())
            self.assertIn(project.id, manager._activating)
            self.assertIn(
                "Activation recovery failed",
                manager.describe_project(project.id)["error"],
            )
            for operation, arguments in (
                (manager.acquire_request, (project.id,)),
                (manager.start, (project.id,)),
                (manager.stop, (project.id,)),
                (manager.activate, (project.id, draft["id"], draft["revision"])),
            ):
                with (
                    self.subTest(operation=operation.__name__),
                    self.assertRaises(ProjectActivatingError),
                ):
                    operation(*arguments)
            journal = project.directory / ".reqlica" / "activation.json"
            self.assertEqual(json.loads(journal.read_text())["phase"], "backed_up")
            self.assertTrue((journal.parent / "activation-backup.sqlite").exists())
            self.assertEqual(marker.read_text(), "live import ran")
            self.assertNotEqual(self.database_state(project), before)
            self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        manager.close()
        self.assertEqual(manager.workers, {})
        self.assertNotIn(
            candidate_pid, {child.pid for child in multiprocessing.active_children()}
        )
        recovered = self.manager()
        self.assert_hello(
            recovered.get_or_start(project.id), self.default_response(project)
        )
        self.assertEqual(get_project(project.id), project)
        self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        self.assertEqual(content_digest(code_directory(project)), old_code)
        self.assertEqual(self.database_state(project), before)
        self.assert_clean(project)

    def test_unready_candidate_cleanup_failure_retains_recovery_state_and_gate(self):
        project = self.project()
        marker = self.root / "unready-candidate-wrote"
        draft = self.approve(
            project,
            self.draft(
                project, {"candidate": True}, self.live_write_source(project, marker)
            ),
        )
        manager = self.manager()
        before = self.database_state(project)
        pointer = (project.directory / "project.json").read_bytes()
        children = {child.pid for child in multiprocessing.active_children()}
        state = project.directory / ".reqlica"
        candidates = []
        stop_worker = manager._stop_worker

        def cleanup_candidates():
            manager.close()
            for worker in candidates:
                if not worker.closed:
                    stop_worker(worker)

        self.addCleanup(cleanup_candidates)

        def force_timeout(connection, timeout=None):
            self.assertEqual(timeout, manager.startup_timeout)
            deadline = time.monotonic() + 10
            while not (state / "candidate-paused").exists():
                if time.monotonic() >= deadline:
                    self.fail("Spawned candidate did not reach its pre-lock pause")
                time.sleep(0.01)
            return False

        def fail_cleanup(worker):
            if not candidates:
                candidates.append(worker)
            self.assertIs(worker, candidates[0])
            self.assertFalse(worker.closed)
            self.assertTrue(worker.process.is_alive())
            raise OSError("unready candidate cleanup failed")

        with (
            patch("reqlica.runtime.worker._run_worker", _paused_run_worker),
            patch.object(
                Connection, "poll", autospec=True, side_effect=force_timeout
            ) as timeout_poll,
            patch.object(manager, "_stop_worker", side_effect=fail_cleanup) as stop,
            patch(
                "reqlica.runtime.activation.recover_activation",
                wraps=recover_activation,
            ) as recover,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "recovery failed.*unready candidate cleanup failed"
            ):
                manager.activate(project.id, draft["id"], draft["revision"])
            self.assertEqual(timeout_poll.call_count, 1)
            self.assertEqual(stop.call_count, 2)
            # No restore may run while a retained child can still acquire the lock.
            self.assertEqual(recover.call_count, 1)
            candidate = candidates[0]
            candidate_pid = candidate.process.pid
            self.assertIs(manager.workers[project.id], candidate)
            self.assertIsNone(candidate.manifest)
            self.assertIn(project.id, manager._activating)
            self.assertIn("Activation recovery failed", manager._errors[project.id])
            with (state / "runtime.lock").open("rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
            self.assertEqual(
                json.loads((state / "activation.json").read_text())["phase"],
                "backed_up",
            )
            self.assertTrue((state / "activation-backup.sqlite").exists())
            self.assertFalse(marker.exists())
            self.assertEqual(self.database_state(project), before)
            self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
            for operation, arguments in (
                (manager.get_or_start, (project.id,)),
                (manager.acquire_request, (project.id,)),
                (manager.start, (project.id,)),
                (manager.stop, (project.id,)),
                (manager.activate, (project.id, draft["id"], draft["revision"])),
            ):
                with (
                    self.subTest(operation=operation.__name__),
                    self.assertRaises(ProjectActivatingError),
                ):
                    operation(*arguments)
        manager.close()
        self.assertTrue(candidate.closed)
        self.assertEqual(manager.workers, {})
        self.assertNotIn(
            candidate_pid, {child.pid for child in multiprocessing.active_children()}
        )
        (state / "candidate-go").touch()
        recovered = self.manager()
        with patch(
            "reqlica.runtime.activation.restore_database",
            wraps=restore_database,
        ) as restore:
            self.assert_hello(
                recovered.get_or_start(project.id), self.default_response(project)
            )
        restore.assert_called_once_with(
            state / "activation-backup.sqlite", project.directory / "data.sqlite"
        )
        self.assertFalse(marker.exists())
        self.assertEqual(get_project(project.id), project)
        self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        self.assertEqual(self.database_state(project), before)
        self.assert_clean(project)
        recovered.close()
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()}, children
        )

    def test_close_waits_for_old_worker_stop_and_removal_during_activation(self):
        project = self.project()
        other = self.project("other")
        draft = self.approve(project, self.draft(project, {"candidate": True}))
        children = {child.pid for child in multiprocessing.active_children()}
        manager = self.manager()
        old = manager.get_or_start(project.id)
        independent = manager.get_or_start(other.id)
        before = self.database_state(project)
        pointer = (project.directory / "project.json").read_bytes()
        old_closed = threading.Event()
        release_old = threading.Event()
        close_entered = threading.Event()
        close_finished = threading.Event()
        candidates = []
        stop_worker = manager._stop_worker
        start_worker = manager._start_worker

        def cleanup_candidates():
            manager.close()
            for worker in candidates:
                if not worker.closed:
                    stop_worker(worker)

        self.addCleanup(cleanup_candidates)

        def stop_then_pause(worker):
            stop_worker(worker)
            if worker is old:
                self.assertTrue(worker.closed)
                self.assertIs(manager.workers[project.id], worker)
                old_closed.set()
                self.assertTrue(release_old.wait(10), "Old worker removal not released")

        def start_after_close(candidate_project):
            self.assertTrue(close_finished.wait(10), "Concurrent close did not finish")
            worker = start_worker(candidate_project)
            candidates.append(worker)
            return worker

        def close_manager():
            close_entered.set()
            manager.close()
            close_finished.set()

        with (
            patch.object(manager, "_stop_worker", side_effect=stop_then_pause),
            patch.object(manager, "_start_worker", side_effect=start_after_close),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            activation = pool.submit(
                manager.activate, project.id, draft["id"], draft["revision"]
            )
            try:
                self.assertTrue(
                    old_closed.wait(10), "Activation did not stop old worker"
                )
                acquired = manager._lock.acquire(blocking=False)
                if acquired:
                    manager._lock.release()
                self.assertFalse(acquired, "Stop and removal must hold manager._lock")
                shutdown = pool.submit(close_manager)
                self.assertTrue(close_entered.wait(5))
                self.assertFalse(close_finished.wait(0.1))
                self.assertFalse(independent.closed)
            finally:
                release_old.set()
            shutdown.result(timeout=10)
            with self.assertRaisesRegex(RuntimeError, "closed during activation"):
                activation.result(timeout=15)
        self.assertTrue(old.closed)
        self.assertTrue(independent.closed)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].closed)
        self.assertEqual(manager.workers, {})
        self.assertNotIn(project.id, manager._activating)
        self.assertEqual(get_project(project.id), project)
        self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        self.assertEqual(self.database_state(project), before)
        self.assert_clean(project)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()}, children
        )

    def test_close_does_not_leak_started_but_unregistered_activation_candidate(self):
        project = self.project()
        other = self.project("other")
        marker = self.root / "unregistered-candidate-wrote"
        draft = self.approve(
            project,
            self.draft(
                project, {"candidate": True}, self.live_write_source(project, marker)
            ),
        )
        children = {child.pid for child in multiprocessing.active_children()}
        manager = self.manager()
        old = manager.get_or_start(project.id)
        independent = manager.get_or_start(other.id)
        before = self.database_state(project)
        pointer = (project.directory / "project.json").read_bytes()
        started = threading.Event()
        release_candidate = threading.Event()
        candidates = []
        start_worker = manager._start_worker
        stop_worker = manager._stop_worker

        def cleanup_candidates():
            manager.close()
            for worker in candidates:
                if not worker.closed:
                    stop_worker(worker)

        self.addCleanup(cleanup_candidates)

        def start_then_pause(candidate_project):
            worker = start_worker(candidate_project)
            candidates.append(worker)
            started.set()
            self.assertTrue(
                release_candidate.wait(10), "Started candidate was not released"
            )
            return worker

        with (
            patch.object(manager, "_start_worker", side_effect=start_then_pause),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            activation = pool.submit(
                manager.activate, project.id, draft["id"], draft["revision"]
            )
            try:
                self.assertTrue(started.wait(10), "Candidate did not report readiness")
                candidate = candidates[0]
                self.assertIsNotNone(candidate.manifest)
                self.assertNotIn(project.id, manager.workers)
                self.assertEqual(marker.read_text(), "live import ran")
                self.assertNotEqual(self.database_state(project), before)
                manager.close()
                self.assertTrue(manager._closed)
                self.assertTrue(old.closed)
                self.assertTrue(independent.closed)
                self.assertEqual(manager.workers, {})
                self.assertFalse(candidate.closed)
                self.assertTrue(candidate.process.is_alive())
            finally:
                release_candidate.set()
            with self.assertRaisesRegex(RuntimeError, "closed during activation"):
                activation.result(timeout=15)
        self.assertTrue(candidate.closed)
        # Stopping an already closed process handle must remain harmless.
        stop_worker(candidate)
        self.assertEqual(manager.workers, {})
        self.assertNotIn(project.id, manager._activating)
        self.assertEqual(get_project(project.id), project)
        self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        self.assertEqual(self.database_state(project), before)
        self.assert_clean(project)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()}, children
        )

    def test_live_candidate_self_mutation_after_startup_is_rejected_and_rolled_back(
        self,
    ):
        project = self.project()
        marker = self.root / "self-mutation-ran"
        source = self.live_write_source(
            project,
            marker,
            "    source = Path(__file__)\n"
            "    source.write_text(source.read_text() + '\\n# live source mutation\\n')\n",
        )
        draft = self.approve(project, self.draft(project, {"candidate": True}, source))
        self.assertFalse(marker.exists())
        manager = self.manager()
        manager.get_or_start(project.id)
        before = self.database_state(project)
        pointer = (project.directory / "project.json").read_bytes()
        old_code = content_digest(code_directory(project))
        with self.assertRaisesRegex(
            ValueError, "Published code changed during candidate startup"
        ):
            manager.activate(project.id, draft["id"], draft["revision"])
        self.assertEqual(marker.read_text(), "live import ran")
        self.assertEqual(get_project(project.id), project)
        self.assertEqual((project.directory / "project.json").read_bytes(), pointer)
        self.assertEqual(content_digest(code_directory(project)), old_code)
        self.assertEqual(self.database_state(project), before)
        result = manager.describe_project(project.id)
        self.assertEqual(result["status"], "running")
        self.assertIsNone(result["error"])
        self.assert_hello(manager.workers[project.id], self.default_response(project))
        self.assertNotIn(project.id, manager._activating)
        self.assert_clean(project)

    def test_live_import_write_then_failure_restores_old_code_data_and_worker(self):
        project = self.project()
        marker = self.root / "failed-import-ran"
        live_database = project.directory / "data.sqlite"
        source = (
            "from pathlib import Path\n"
            "from reqlica.runtime.sdk import database_path, transaction\n"
            f"if database_path() == Path({str(live_database)!r}):\n"
            "    with transaction() as database:\n"
            "        database.execute(\"UPDATE records SET value = 'bad import' WHERE id = 1\")\n"
            "        database.execute(\"INSERT INTO records VALUES (2, 'must disappear')\")\n"
            f"    Path({str(marker)!r}).write_text('committed live writes')\n"
            "    raise RuntimeError('candidate import failed after commit')\n"
        )
        draft = self.approve(project, self.draft(project, {"candidate": True}, source))
        self.assertFalse(
            marker.exists(), "Disposable checks must not target the live database"
        )
        manager = self.manager()
        old_pid = manager.get_or_start(project.id).process.pid
        before = self.database_state(project)
        old_code = content_digest(code_directory(project))
        with self.assertRaisesRegex(
            RuntimeError, "candidate import failed after commit"
        ):
            manager.activate(project.id, draft["id"], draft["revision"])
        self.assertEqual(marker.read_text(), "committed live writes")
        self.assertEqual(get_project(project.id), project)
        self.assertEqual(content_digest(code_directory(project)), old_code)
        self.assertEqual(self.database_state(project), before)
        result = manager.describe_project(project.id)
        self.assertEqual(result["status"], "running")
        self.assertIsNone(result["error"])
        self.assertNotEqual(result["pid"], old_pid)
        self.assert_hello(
            manager.get_or_start(project.id), self.default_response(project)
        )
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()}, {result["pid"]}
        )
        self.assertNotIn(project.id, manager._activating)
        self.assert_clean(project)

    def test_schema_changing_import_is_api_conflict_and_rolls_back(self):
        project = self.project()
        marker = self.root / "schema-import-ran"
        source = (
            "from pathlib import Path\n"
            "from reqlica.runtime.sdk import database_path, transaction\n"
            f"if database_path() == Path({str(project.directory / 'data.sqlite')!r}):\n"
            "    with transaction() as database:\n"
            "        database.execute('CREATE TABLE candidate_table (value TEXT)')\n"
            "        database.execute(\"UPDATE records SET value = 'schema candidate'\")\n"
            f"    Path({str(marker)!r}).write_text('changed live schema')\n"
        )
        draft = self.approve(project, self.draft(project, {"candidate": True}, source))
        self.assertFalse(marker.exists())
        before = self.database_state(project)
        old_code = content_digest(code_directory(project))
        with TestClient(app) as client:
            url = f"/mock/{project.id}/hello"
            self.assertEqual(client.get(url).status_code, 200)
            manager = app.state.worker_manager
            old_pid = manager.workers[project.id].process.pid
            response = client.post(
                f"/api/projects/{project.id}/activate", json=self.payload(draft)
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("Schema-changing", response.json()["detail"])
            self.assertEqual(marker.read_text(), "changed live schema")
            self.assertEqual(get_project(project.id), project)
            self.assertEqual(content_digest(code_directory(project)), old_code)
            self.assertEqual(self.database_state(project), before)
            self.assertEqual(client.get(url).json(), self.default_response(project))
            result = manager.describe_project(project.id)
            self.assertEqual(result["status"], "running")
            self.assertIsNone(result["error"])
            self.assertNotEqual(result["pid"], old_pid)
            self.assertEqual(
                {child.pid for child in multiprocessing.active_children()},
                {result["pid"]},
            )
            self.assert_clean(project)

    def test_drain_waits_for_all_leases_rejects_new_work_and_leaves_other_project_responsive(
        self,
    ):
        project = self.project()
        other = self.project("other")
        expected = {"activated": True}
        draft = self.approve(project, self.draft(project, expected))
        with (
            TestClient(app) as client,
            patch("reqlica.runtime.activation.DRAIN_TIMEOUT", 15),
        ):
            manager = app.state.worker_manager
            old = manager.acquire_request(project.id)
            old_pid = old.process.pid
            manager.acquire_request(project.id)
            leases = 2
            other_pid = manager.get_or_start(other.id).process.pid
            before = self.database_state(project)
            with ThreadPoolExecutor(max_workers=3) as pool:
                future = pool.submit(
                    client.post,
                    f"/api/projects/{project.id}/activate",
                    json=self.payload(draft),
                )
                try:
                    self.wait_activating(manager, project.id, future)
                    self.assertFalse(future.done())
                    self.assertEqual(get_project(project.id), project)
                    self.assertTrue(old.process.is_alive())
                    self.assertEqual(self.database_state(project), before)
                    for operation, arguments in (
                        (manager.start, (project.id,)),
                        (manager.stop, (project.id,)),
                        (manager.get_or_start, (project.id,)),
                        (manager.acquire_request, (project.id,)),
                        (
                            manager.activate,
                            (project.id, draft["id"], draft["revision"]),
                        ),
                    ):
                        with (
                            self.subTest(operation=operation.__name__),
                            self.assertRaises(ProjectActivatingError),
                        ):
                            operation(*arguments)
                    self.assertEqual(manager._requests[project.id], 2)
                    blocked = client.get(f"/mock/{project.id}/hello")
                    self.assertEqual(blocked.status_code, 503, blocked.text)
                    self.assertIn("activating", blocked.json()["detail"])
                    for action in ("start", "stop", "activate"):
                        response = client.post(
                            f"/api/projects/{project.id}/{action}",
                            json=self.payload(draft) if action == "activate" else None,
                        )
                        self.assertEqual(response.status_code, 503, response.text)
                    independent = pool.submit(
                        client.get, f"/mock/{other.id}/hello"
                    ).result(timeout=5)
                    self.assertEqual(independent.status_code, 200, independent.text)
                    self.assertEqual(independent.json(), self.default_response(other))
                    self.assertEqual(manager.workers[other.id].process.pid, other_pid)
                    self.assertFalse(future.done())
                    manager.release_request(project.id)
                    leases -= 1
                    self.assertEqual(manager._requests[project.id], 1)
                    self.assertFalse(future.done())
                    self.assertTrue(old.process.is_alive())
                finally:
                    for _ in range(leases):
                        manager.release_request(project.id)
                response = future.result(timeout=15)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "running")
            self.assertNotEqual(response.json()["pid"], old_pid)
            self.assertEqual(client.get(f"/mock/{project.id}/hello").json(), expected)
            self.assertEqual(manager.workers[other.id].process.pid, other_pid)
            self.assertNotIn(project.id, manager._requests)
            self.assertNotIn(project.id, manager._activating)
            self.assertEqual(self.database_state(project), before)
            self.assert_clean(project)

    def test_drain_timeout_retains_old_worker_and_allows_retry_after_release(self):
        project = self.project()
        draft = self.approve(project, self.draft(project, {"retry": True}))
        before = self.database_state(project)
        with TestClient(app) as client:
            manager = app.state.worker_manager
            old = manager.acquire_request(project.id)
            old_pid = old.process.pid
            try:
                with patch("reqlica.runtime.activation.DRAIN_TIMEOUT", 0.1):
                    response = client.post(
                        f"/api/projects/{project.id}/activate", json=self.payload(draft)
                    )
                self.assertEqual(response.status_code, 502, response.text)
                self.assertIn("timed out draining", response.json()["detail"])
                self.assertEqual(get_project(project.id), project)
                self.assertIs(manager.get_or_start(project.id), old)
                self.assertEqual(old.process.pid, old_pid)
                self.assert_hello(old, self.default_response(project))
                self.assertEqual(self.database_state(project), before)
                self.assertEqual(manager._requests[project.id], 1)
                self.assertNotIn(project.id, manager._activating)
                self.assert_clean(project)
            finally:
                manager.release_request(project.id)
            self.assertEqual(
                client.get(f"/mock/{project.id}/hello").json(),
                self.default_response(project),
            )
            result = manager.activate(project.id, draft["id"], draft["revision"])
            self.assertEqual(result["status"], "running")
            self.assert_hello(manager.get_or_start(project.id), {"retry": True})
            self.assert_clean(project)

    def test_interrupted_prepared_and_backed_up_journals_recover_before_lazy_start(
        self,
    ):
        for phase in ("prepared", "backed_up"):
            with self.subTest(phase=phase):
                project = self.project(phase)
                before = self.database_state(project)
                old_code = content_digest(code_directory(project))
                candidate, _ = self.inject_journal(project, phase)
                self.assertEqual(get_project(project.id), candidate)
                if phase == "backed_up":
                    self.assertNotEqual(self.database_state(project), before)
                with TestClient(app) as client:
                    manager = app.state.worker_manager
                    self.assertEqual(manager.workers, {})
                    response = client.get(f"/mock/{project.id}/hello")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json(), self.default_response(project))
                    self.assertEqual(get_project(project.id), project)
                    self.assertEqual(manager.workers[project.id].project, project)
                    self.assertEqual(self.database_state(project), before)
                    self.assertEqual(content_digest(code_directory(project)), old_code)
                    self.assert_clean(project)
                    pid = manager.workers[project.id].process.pid
                    self.assertEqual(
                        client.get(f"/mock/{project.id}/hello").status_code, 200
                    )
                    self.assertEqual(manager.workers[project.id].process.pid, pid)

    def test_committed_journal_cleanup_retains_new_pointer_and_data(self):
        project = self.project()
        old_data = self.database_state(project)
        candidate, expected = self.inject_journal(project, "committed")
        committed_data = self.database_state(project)
        self.assertNotEqual(committed_data, old_data)
        with TestClient(app) as client:
            response = client.get(f"/mock/{project.id}/hello")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            self.assertEqual(get_project(project.id), candidate)
            self.assertEqual(
                app.state.worker_manager.workers[project.id].project, candidate
            )
            self.assertEqual(self.database_state(project), committed_data)
            self.assert_clean(project)

    def test_recovered_journal_only_cleans_up_and_invalid_journal_fails_closed(self):
        project = self.project()
        before = self.database_state(project)
        self.inject_journal(project, "recovered")
        manager = self.manager()
        self.assert_hello(
            manager.get_or_start(project.id), self.default_response(project)
        )
        self.assertEqual(get_project(project.id), project)
        self.assertEqual(self.database_state(project), before)
        self.assert_clean(project)

        invalid = self.project("invalid")
        self.inject_journal(invalid, "prepared")
        journal = invalid.directory / ".reqlica" / "activation.json"
        record = json.loads(journal.read_text())
        original_pointer = get_project(invalid.id)
        original_data = self.database_state(invalid)
        for corruption in ("phase", "project ID"):
            with self.subTest(corruption=corruption):
                injected = json.loads(json.dumps(record))
                if corruption == "phase":
                    injected["phase"] = "unknown"
                else:
                    injected["previous"]["id"] = uuid4().hex
                atomic_write_json(journal, injected)
                with self.assertRaisesRegex(
                    ValueError, "Invalid activation recovery journal"
                ):
                    manager.get_or_start(invalid.id)
                self.assertNotIn(invalid.id, manager.workers)
                self.assertEqual(get_project(invalid.id), original_pointer)
                self.assertEqual(self.database_state(invalid), original_data)
                self.assertEqual(json.loads(journal.read_text()), injected)

    def test_activation_api_unknown_ids_conflicts_and_request_validation(self):
        project = self.project()
        draft = self.draft(project)
        with TestClient(app) as client:
            for project_id in (uuid4().hex, "invalid-id"):
                response = client.post(
                    f"/api/projects/{project_id}/activate", json=self.payload(draft)
                )
                self.assertEqual(response.status_code, 404, response.text)
            response = client.post(
                f"/api/projects/{project.id}/activate",
                json={"draft_id": uuid4().hex, "expected_revision": draft["revision"]},
            )
            self.assertEqual(response.status_code, 404, response.text)
            response = client.post(
                f"/api/projects/{project.id}/activate", json=self.payload(draft)
            )
            self.assertEqual(response.status_code, 409, response.text)
            stale = {**self.payload(draft), "expected_revision": "0" * 64}
            response = client.post(f"/api/projects/{project.id}/activate", json=stale)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("revision conflict", response.json()["detail"])
            for payload in (
                {},
                {"draft_id": draft["id"]},
                {**self.payload(draft), "draft_id": "../unsafe"},
                {**self.payload(draft), "expected_revision": "bad"},
                {**self.payload(draft), "unexpected": True},
            ):
                with self.subTest(payload=payload):
                    response = client.post(
                        f"/api/projects/{project.id}/activate", json=payload
                    )
                    self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(app.state.worker_manager.workers, {})
            self.assertEqual(get_project(project.id), project)
            self.assertNotIn(project.id, app.state.worker_manager._activating)
            self.assert_clean(project)

    @unittest.skipUnless(hasattr(os, "setsid"), "Requires POSIX process groups")
    def test_hard_killed_gateway_leaves_no_writable_orphan_before_recovery(self):
        project = self.project()
        before = self.database_state(project)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        gateway = context.Process(target=_dedicated_gateway, args=(project.id, child))

        def cleanup_gateway():
            if gateway.pid is not None:
                if gateway.is_alive():
                    gateway.kill()
                gateway.join(timeout=5)
                # The dedicated process group also bounds cleanup if the guard fails.
                try:
                    os.killpg(gateway.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                gateway.close()

        self.addCleanup(cleanup_gateway)
        gateway.start()
        child.close()
        self.assertTrue(parent.poll(15), "Dedicated gateway did not start its worker")
        orphan_pid, port = parent.recv()
        with httpx.Client(trust_env=False, timeout=1) as client:
            worker_url = f"http://127.0.0.1:{port}/hello"
            self.assertEqual(
                client.get(worker_url).json(), self.default_response(project)
            )
            lock = project.directory / ".reqlica" / "runtime.lock"
            with lock.open("rb") as descriptor, self.assertRaises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.inject_journal(project, "backed_up")
            gateway.kill()
            gateway.join(timeout=5)
            self.assertFalse(gateway.is_alive())
            self.assertEqual(gateway.exitcode, -signal.SIGKILL)
            deadline = time.monotonic() + 5
            while True:
                try:
                    client.get(worker_url)
                except httpx.TransportError:
                    break
                if time.monotonic() >= deadline:
                    self.fail(
                        f"Worker {orphan_pid} still accepts requests after parent death"
                    )
                time.sleep(0.02)
        with lock.open("rb") as descriptor:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        manager = self.manager()
        worker = manager.get_or_start(project.id)
        self.assertNotEqual(worker.process.pid, orphan_pid)
        self.assert_hello(worker, self.default_response(project))
        self.assertEqual(get_project(project.id), project)
        self.assertEqual(self.database_state(project), before)
        self.assert_clean(project)


if __name__ == "__main__":
    unittest.main()
