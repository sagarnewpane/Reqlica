"""Baseline resets preserve schema, fence live workers, and retain recovery data."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from reqlica.projects.scenarios import get_scenarios
from reqlica.runtime.app import app as gateway
from test_scenarios import ScenarioFixture


class ResetTests(ScenarioFixture):
    def setUp(self):
        super().setUp()
        self.database = self.project.directory / "data.sqlite"
        self.baseline_url = f"/api/projects/{self.project.id}/data-baseline"
        self.reset_url = f"/api/projects/{self.project.id}/data-reset"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
            db.execute("INSERT INTO items(name) VALUES('original')")

    def rows(self, database=None):
        with closing(sqlite3.connect(database or self.database)) as db:
            return db.execute("SELECT id, name FROM items ORDER BY id").fetchall()

    def capture(self):
        info = self.console.get(self.baseline_url).json()
        reply = self.console.put(self.baseline_url, json={"expected_revision":info["revision"], "confirm":True})
        self.assertEqual(reply.status_code, 200, reply.text)
        info = self.console.get(self.baseline_url).json()
        return {"expected_revision":info["revision"], "expected_schema":info["current_schema_fingerprint"], "confirm":True}

    def modify(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO items(name) VALUES('new')")

    def test_restore_running_project_backup_and_explicit_restart(self):
        body = self.capture()
        self.modify()
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        reply = self.gateway.post(self.reset_url, json=body)
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(self.rows(), [(1, "original")])
        backup = self.project.directory / reply.json()["backup"]
        self.assertEqual(self.rows(backup), [(1,"original"), (2,"new")])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
        self.assertEqual(self.gateway.post(f"/api/projects/{self.project.id}/start").status_code, 200)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)

    def test_missing_confirmation_stale_revision_and_schema_change(self):
        body = self.capture()
        self.modify()
        for confirmation in (False, 1, "true", None):
            reply = self.gateway.post(self.reset_url, json={**body, "confirm":confirmation})
            self.assertEqual(reply.status_code, 422)
        self.assertEqual(self.gateway.post(self.reset_url, json={**body, "expected_revision":"f" * 32}).status_code, 409)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE later(id INTEGER)")
        self.assertEqual(self.gateway.post(self.reset_url, json=body).status_code, 409)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)

    def test_baseline_replacement_and_tamper_detection(self):
        old = self.capture()
        self.modify()
        new = self.capture()
        self.assertNotEqual(old["expected_revision"], new["expected_revision"])
        self.assertEqual(self.gateway.post(self.reset_url, json=old).status_code, 409)
        baseline = self.project.directory / f".reqlica/data-baselines/{new['expected_revision']}.sqlite"
        baseline.write_bytes(b"tampered")
        self.assertEqual(self.gateway.post(self.reset_url, json=new).status_code, 409)
        self.assertEqual(len(self.rows()), 2)

    def test_drain_fences_new_requests_and_preserves_other_projects(self):
        body = self.capture()
        self.modify()
        manager = gateway.state.worker_manager
        manager.acquire_request(self.project.id)
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(self.gateway.post, self.reset_url, json=body)
            try:
                deadline = time.monotonic() + 3
                while self.project.id not in manager._activating and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIn(self.project.id, manager._activating)
                self.assertFalse(future.done())
                self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
                self.assertEqual(self.gateway.get(f"/mock/{self.other.id}/hello").status_code, 200)
            finally:
                manager.release_request(self.project.id)
            self.assertEqual(future.result().status_code, 200)
        self.assertEqual(self.rows(), [(1,"original")])

    def test_drain_timeout_and_snapshot_failure_leave_data_intact(self):
        body = self.capture()
        self.modify()
        manager = gateway.state.worker_manager
        manager.acquire_request(self.project.id)
        try:
            with patch("reqlica.runtime.reset.DRAIN_TIMEOUT", 0):
                self.assertEqual(self.gateway.post(self.reset_url, json=body).status_code, 503)
        finally:
            manager.release_request(self.project.id)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        with patch("reqlica.projects.reset.backup_database", side_effect=OSError("disk full")):
            self.assertEqual(self.gateway.post(self.reset_url, json=body).status_code, 503)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)

    def test_wal_capture_and_console_forwarding(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("INSERT INTO items(name) VALUES('wal')")
            db.commit()
            body = self.capture()
        self.modify()
        with patch("reqlica.console.routes.runtime.gateway_operation", side_effect=lambda project, command, payload: self.gateway.post(f"/api/projects/{project}/{command}", json=payload).json()):
            reply = self.console.post(self.reset_url, json=body)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.rows(), [(1,"original"), (2,"wal")])

    def test_scenario_reset_retains_rules_and_rearms_counters(self):
        self.configure({"method":"GET", "path":"/hello", "response":"failure", "failure_count":1,
                        "rate_limit":{"requests":1, "window_seconds":60}})
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 429)
        before = get_scenarios(self.project.id)
        url = f"/api/projects/{self.project.id}/scenarios/reset"
        reply = self.console.post(url, json={"expected_revision":before["revision"], "confirm":True})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertNotEqual(reply.json()["revision"], before["revision"])
        self.assertEqual(reply.json()["rules"][0]["remaining_failures"], 1)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
        self.assertEqual(self.console.post(url, json={"expected_revision":before["revision"], "confirm":True}).status_code, 409)
        self.assertEqual(self.rows(), [(1,"original")])
