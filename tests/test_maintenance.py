"""Remaining maintenance features preserve identity and isolate live operations."""

import io
import json
import os
import sqlite3
import zipfile
from contextlib import closing

from reqlica.projects import drafts
from reqlica.projects.capture import bounded_payload
from reqlica.projects.inspection import read_logs
from reqlica.projects.reset import _fingerprint
from reqlica.projects.store import get_project, registry_directory
from reqlica.projects.versions import code_directory, content_digest, atomic_write_json
from reqlica.projects.workspace import import_workspace
from test_scenarios import ScenarioFixture


class MaintenanceTests(ScenarioFixture):
    def setUp(self):
        super().setUp()
        self.database = self.project.directory / "data.sqlite"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO records VALUES(1, 'saved')")

    def test_dashboard_maintenance_state_tracks_active_source_without_starting_worker(self):
        url = f"/api/projects/{self.project.id}/maintenance"
        response = self.console.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {"project_id": self.project.id,
                         "directory": str(self.project.directory),
                         "active_revision": content_digest(code_directory(self.project))})
        self.assertNotIn("pid", response.json())
        (code_directory(self.project) / "endpoints/hello.py").write_text("def hello():\n    return {'changed': True}\n")
        self.assertNotEqual(self.console.get(url).json()["active_revision"], response.json()["active_revision"])
        self.assertEqual(self.console.get("/api/projects/" + "0" * 32 + "/maintenance").status_code, 404)

    def test_relocate_preserves_full_project_and_restart(self):
        self.gateway.get(self.mock + "/hello")
        target = self.root / "relocated"
        body = {"directory":str(target), "expected_directory":str(self.project.directory),
                "expected_revision":content_digest(code_directory(self.project)), "confirm":True}
        reply = self.gateway.post(f"/api/projects/{self.project.id}/relocate", json=body)
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertFalse(self.project.directory.exists())
        self.assertEqual(get_project(self.project.id).directory, target)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
        self.assertEqual(self.gateway.post(f"/api/projects/{self.project.id}/start").status_code, 200)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)

    def test_relocation_crash_recovers_registry_without_execution(self):
        target = self.root / "moved-after-crash"
        atomic_write_json(registry_directory() / f".relocate-{self.project.id}.json", {"source":str(self.project.directory),"target":str(target)})
        os.rename(self.project.directory, target)
        self.assertEqual(get_project(self.project.id).directory, target)
        self.assertFalse((registry_directory() / f".relocate-{self.project.id}.json").exists())

    def test_full_workspace_preserves_drafts_and_opt_in_secrets(self):
        draft = drafts.create_draft(self.project.id)
        env = f"/api/projects/{self.project.id}/environment"
        revision = self.console.get(env).json()["revision"]
        self.console.put(env + "/JWT_SECRET", json={"expected_revision":revision,"value":"private-workspace-secret"})
        body = {"expected_revision":content_digest(code_directory(self.project)),"confirm":True}
        url = f"/api/projects/{self.project.id}/workspace-export"
        public = self.gateway.post(url, json=body)
        self.assertEqual(public.status_code, 200, public.text[:200])
        with zipfile.ZipFile(io.BytesIO(public.content)) as archive:
            self.assertNotIn(".reqlica/environment.json", archive.namelist())
            self.assertIn(f"drafts/{draft['id']}/manifest.json", archive.namelist())
        private = self.gateway.post(url, json={**body,"include_secrets":True})
        self.assertEqual(private.status_code, 200)
        self.gateway.request("DELETE", f"/api/projects/{self.project.id}", json={"delete_files":False})
        result = import_workspace(private.content, self.root / "restored")
        restored = get_project(result["project_id"])
        self.assertTrue((restored.directory / f"drafts/{draft['id']}/manifest.json").exists())
        self.assertIn("private-workspace-secret", (restored.directory / ".reqlica/environment.json").read_text())

    def preview(self, operations):
        reply = self.console.post(f"/api/projects/{self.project.id}/database-migrations/preview", json={"expected_schema":_fingerprint(self.database),"operations":operations})
        self.assertEqual(reply.status_code, 200, reply.text)
        return reply.json()

    def checked_draft(self, preview):
        draft = drafts.create_draft(self.project.id)
        draft = drafts.write_draft_file(self.project.id, draft["id"], "database/migration.json", json.dumps(preview["plan"]), draft["revision"])
        validation = drafts.validate_draft(self.project.id, draft["id"], draft["revision"])
        self.assertEqual(validation["status"], "passed", validation)
        tests = drafts.test_draft(self.project.id, draft["id"], draft["revision"])
        self.assertEqual(tests["status"], "passed", tests)
        return draft

    def test_add_column_migration_checks_apply_and_rollback(self):
        preview = self.preview([{"op":"add_column","table":"records","column":{"name":"note","type":"TEXT"}}])
        draft = self.checked_draft(preview)
        self.assertEqual(_fingerprint(self.database), preview["plan"]["from_schema"])
        body = {"expected_revision":draft["revision"], "draft_id":draft["id"],"expected_schema":preview["plan"]["from_schema"],
                "expected_plan_digest":preview["plan_digest"],"confirm":True}
        reply = self.gateway.post(f"/api/projects/{self.project.id}/database-migrations/apply", json=body)
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(_fingerprint(self.database), preview["target_schema"])
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE records SET value='after migration'")
        rollback = self.gateway.post(f"/api/projects/{self.project.id}/database-migrations/rollback", json={"expected_revision":preview["plan"]["id"],"expected_schema":preview["target_schema"],"confirm":True})
        self.assertEqual(rollback.status_code, 200, rollback.text)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT * FROM records").fetchall(), [(1,"saved")])
        self.assertEqual(_fingerprint(self.database), preview["plan"]["from_schema"])

    def test_rebuild_rename_drop_and_constraint_failures(self):
        operations = [{"op":"rebuild_table","table":"records", "definition":{"name":"records","columns":[
            {"name":"id","type":"INTEGER","primary_key":True}, {"name":"renamed","type":"TEXT","nullable":False}]},
            "copy_columns":{"id":"id","renamed":"value"}}]
        preview = self.preview(operations)
        draft = self.checked_draft(preview)
        from reqlica.projects.migrations import prepare_migration
        from reqlica.runtime.sdk.database import backup_database
        copied = self.root / "copy.sqlite"
        backup_database(self.database, copied)
        prepare_migration(drafts.draft_directory(self.project, draft["id"]), copied)
        self.assertEqual(_fingerprint(copied), preview["target_schema"])
        with closing(sqlite3.connect(copied)) as db:
            self.assertEqual(db.execute("SELECT * FROM records").fetchall(), [(1,"saved")])
        self.preview([{"op":"rename_column","table":"records","name":"value","new_name":"renamed"}])
        self.preview([{"op":"rename_table","table":"records","new_name":"new_records"}])
        self.preview([{"op":"drop_column","table":"records","name":"value"}])
        self.preview([{"op":"drop_table","table":"records"}])
        reply = self.console.post(f"/api/projects/{self.project.id}/database-migrations/preview", json={"expected_schema":_fingerprint(self.database),"operations":[{"op":"rename_table","table":"records; DROP TABLE x","new_name":"safe"}]})
        self.assertEqual(reply.status_code, 409)

    def test_capture_redaction_and_worker_output(self):
        url = f"/api/projects/{self.project.id}/logs/settings"
        revision = self.console.get(url).json()["revision"]
        self.assertEqual(self.console.put(url, json={"expected_revision":revision,"capture_payloads":True,"capture_output":True}).status_code, 200)
        (code_directory(self.project) / "endpoints/hello.py").write_text('import sys\ndef hello():\n    print("captured stdout")\n    print("captured stderr", file=sys.stderr)\n    return {"password":"never expose", "ok":True}\n')
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        events = read_logs(self.project.id)["events"]
        self.assertIn("stdout", {e["kind"] for e in events})
        self.assertIn("stderr", {e["kind"] for e in events})
        request = next(e for e in events if e["kind"] == "request")
        self.assertNotIn("never expose", json.dumps(request))
        self.assertIn("[REDACTED]", request["response_body"]["text"])
        payload = bounded_payload(b'{"token":"secret", "other":"env-secret"}', 1024, {"JWT_SECRET":"env-secret"})
        self.assertNotIn("env-secret", payload["text"])
        self.assertNotIn('"secret"', payload["text"])

    def test_stale_maintenance_does_not_stop_running_worker(self):
        self.gateway.get(self.mock + "/hello")
        reply = self.gateway.post(f"/api/projects/{self.project.id}/relocate", json={
            "expected_revision":"f" * 64,"expected_directory":str(self.project.directory),
            "directory":str(self.root / "stale-target"),"confirm":True})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        self.assertTrue(self.project.directory.exists())

    def test_prepared_receipt_recovery_and_corrupt_rollback_backup(self):
        preview = self.preview([{"op":"drop_column","table":"records","name":"value"}])
        draft = self.checked_draft(preview)
        prefix = f"/api/projects/{self.project.id}/database-migrations"
        body = {"expected_revision":draft["revision"], "draft_id":draft["id"], "expected_schema":preview["plan"]["from_schema"],
                "expected_plan_digest":preview["plan_digest"],"confirm":True}
        reply = self.gateway.post(prefix + "/apply", json=body)
        self.assertEqual(reply.status_code, 200, reply.text)
        receipt = self.project.directory / f".reqlica/migrations/{preview['plan']['id']}.json"
        record = json.loads(receipt.read_text())
        atomic_write_json(receipt, {**record,"phase":"prepared"})
        recovery = {"expected_revision":record["id"],"expected_schema":record["target_schema"],"confirm":True}
        self.assertEqual(self.gateway.post(prefix + "/recover", json=recovery).json()["phase"], "applied")
        backup = self.project.directory / record["backup"]
        with closing(sqlite3.connect(backup)) as db, db:
            db.execute("UPDATE records SET value='modified'")
        self.assertEqual(self.gateway.post(prefix + "/rollback", json=recovery).status_code, 409)

    def test_failed_constraint_preview_never_changes_live_data(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO records VALUES(2, 'saved')")
        before = _fingerprint(self.database)
        reply = self.console.post(f"/api/projects/{self.project.id}/database-migrations/preview", json={"expected_schema":before,"operations":[{
            "op":"rebuild_table","table":"records","definition":{"name":"records","columns":[
                {"name":"id","type":"INTEGER","primary_key":True},{"name":"value","type":"TEXT","unique":True}]},
            "copy_columns":{"id":"id","value":"value"}}]})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(_fingerprint(self.database), before)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records").fetchone()[0], 2)
