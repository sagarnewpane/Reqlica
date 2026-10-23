"""Exercise operations through HTTP MCP and the real gateway/domain boundaries."""

import json
import os
import sqlite3
import zipfile
from contextlib import closing
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation import agent
from reqlica.projects.authentication import AuthWrite, get_authentication, write_authentication
from reqlica.projects.store import get_project
from reqlica.projects.versions import code_directory, content_digest
import test_mcp as protocol
from test_scenarios import ScenarioFixture


class MCPOperationTests(ScenarioFixture):
    rpc = protocol.MCPTests.rpc
    tool = protocol.MCPTests.tool
    tool_error = protocol.MCPTests.tool_error

    def setUp(self):
        super().setUp()
        self.client = self.enterContext(TestClient(create_app(), base_url="http://127.0.0.1:3000"))
        self.request_id = 0
        self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "operations-test", "version": "1"}})
        self.database = self.project.directory / "data.sqlite"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO records VALUES(1, 'saved')")
        def gateway_request(method, url, **kwargs):
            return self.gateway.request(method, urlsplit(url).path, json=kwargs.get("json"))
        self.enterContext(patch("reqlica.mcp.operations.httpx.request", side_effect=gateway_request))
        self.ids = {"project_id": self.project.id}
        self.revision = content_digest(code_directory(self.project))

    def test_traffic_round_trip_redacts_credentials_and_resets_failures_and_quotas(self):
        write_authentication(self.project.id, AuthWrite(expected_revision=get_authentication(self.project.id)["revision"],
            mode="bearer", credentials=[{"name": "tester", "value": "never-return-this-token"}]))
        state = self.tool("get_traffic_policies", self.ids)
        self.assertNotIn("never-return-this-token", json.dumps(state))
        self.assertNotIn("digest", json.dumps(state))
        self.assertEqual(state["authentication"]["credentials"][0]["name"], "tester")
        args = {**self.ids, "policy": "scenarios", "expected_revision": state["scenarios"]["revision"],
                "settings_json": json.dumps({"rules": [{"method": "GET", "path": "/hello", "response": "failure",
                    "failure_count": 1, "rate_limit": {"requests": 2, "window_seconds": 60}}]}), "confirm": True}
        self.tool_error("set_traffic_policy", {**args, "confirm": False}, "confirmation")
        state = self.tool("set_traffic_policy", args)
        self.tool_error("set_traffic_policy", args, "changed")
        headers = {"Authorization": "Bearer never-return-this-token"}
        self.assertEqual([self.gateway.get(self.mock + "/hello", headers=headers).status_code for _ in range(3)], [503, 200, 429])
        reset = self.tool("reset_scenario_counters", {**self.ids, "expected_revision": state["revision"], "confirm": True})
        self.assertNotEqual(reset["revision"], state["revision"])
        self.assertEqual(self.gateway.get(self.mock + "/hello", headers=headers).status_code, 503)

    def test_cors_and_policy_validation_preserve_current_state(self):
        state = self.tool("get_traffic_policies", self.ids)
        args = {**self.ids, "policy": "cors", "expected_revision": state["cors"]["revision"], "confirm": True,
                "settings_json": json.dumps({"enabled": True, "allow_origins": ["http://localhost:5173"]})}
        saved = self.tool("set_traffic_policy", args)
        self.assertTrue(saved["enabled"])
        bad = {**args, "expected_revision": saved["revision"], "settings_json": '{"enabled":true,"allow_origins":["*"],"allow_credentials":true}'}
        self.tool_error("set_traffic_policy", bad, "explicit origins")
        self.tool_error("set_traffic_policy", {**bad, "settings_json": '{"expected_revision":"override"}'}, "without expected_revision")
        self.tool_error("set_traffic_policy", {**bad, "settings_json": '[]'}, "settings object")
        self.assertEqual(self.tool("get_traffic_policies", self.ids)["cors"], saved)

    def test_worker_control_checks_revision_and_confirmation_and_never_publishes(self):
        state = self.tool("get_worker_status", self.ids)
        self.assertEqual(state["active_revision"], self.revision)
        self.assertIsNone(state["pid"])
        args = {**self.ids, "action": "start", "expected_revision": state["active_revision"], "confirm": True}
        self.tool_error("control_project_worker", {**args, "confirm": False}, "confirmation")
        self.tool_error("control_project_worker", {**args, "expected_revision": "f" * 64}, "changed")
        running = self.tool("control_project_worker", args)
        self.assertEqual(running["status"], "running")
        restarted = self.tool("control_project_worker", {**args, "action": "restart"})
        self.assertNotEqual(running["pid"], restarted["pid"])
        self.tool("control_project_worker", {**args, "action": "stop"})
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
        self.assertEqual(self.tool("get_worker_status", self.ids)["status"], "stopped")
        self.assertEqual(get_project(self.project.id).active_version, self.project.active_version)

    def test_baseline_capture_and_live_reset_preserve_backup_and_stop_worker(self):
        baseline = self.tool("get_data_baseline", self.ids)
        self.assertIsNone(baseline["baseline"])
        args = {**self.ids, "expected_revision": baseline["revision"], "confirm": True}
        self.tool_error("capture_data_baseline", {**args, "confirm": False}, "confirmation")
        saved = self.tool("capture_data_baseline", args)
        self.tool_error("capture_data_baseline", args, "changed")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE records SET value='modified'")
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        reset = {**self.ids, "expected_revision": saved["revision"],
                 "expected_schema": baseline["current_schema_fingerprint"], "confirm": True}
        self.tool_error("reset_project_data", {**reset, "expected_schema": "f" * 64}, "schemas must match")
        result = self.tool("reset_project_data", reset)
        self.assertEqual(result["worker_status"], "stopped")
        self.assertTrue((self.project.directory / result["backup"]).is_file())
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT value FROM records").fetchone(), ("saved",))
        with closing(sqlite3.connect(self.project.directory / result["backup"])) as db:
            self.assertEqual(db.execute("SELECT value FROM records").fetchone(), ("modified",))
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)

    def test_migration_history_is_paged_and_does_not_read_other_pages(self):
        directory = self.project.directory / ".reqlica/migrations"
        directory.mkdir(parents=True, exist_ok=True)
        for i, phase in enumerate(["applied", "rolled_back", "prepared"]):
            (directory / f"{i:032x}.json").write_text(json.dumps({"id": f"{i:032x}", "phase": phase}))
        page = self.tool("list_database_migrations", {**self.ids, "limit": 2})
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["next_offset"], 2)
        (directory / ("0" * 32 + ".json")).write_text("invalid")
        last = self.tool("list_database_migrations", {**self.ids, "limit": 2, "offset": page["next_offset"]})
        self.assertEqual(last["migrations"][0]["phase"], "prepared")
        self.assertIsNone(last["next_offset"])
        self.assertIsNone(self.tool("get_worker_status", self.ids)["pid"])

    def export(self, name="project.zip", **kwargs):
        return self.tool("export_project_archive", {**self.ids, "archive_path": str(self.root / name),
            "expected_revision": self.revision, "confirm": True, **kwargs})

    def test_active_archive_round_trip_clone_and_digest_conflict(self):
        exported = self.export()
        self.assertEqual(os.stat(exported["archive_path"]).st_mode & 0o777, 0o600)
        inspected = self.tool("inspect_project_archive", {"archive_path": exported["archive_path"]})
        self.assertEqual(exported["sha256"], inspected["sha256"])
        self.assertFalse(inspected["fully_validated"])
        self.assertNotIn("records", json.dumps(inspected))
        args = {"archive_path": exported["archive_path"], "expected_sha256": inspected["sha256"],
                "directory": str(self.root / "clone"), "clone": True, "name": "Copied API", "confirm": True}
        self.tool_error("import_project_archive", {**args, "expected_sha256": "f" * 64}, "changed")
        self.assertFalse((self.root / "clone").exists())
        clone = self.tool("import_project_archive", args)
        self.assertNotEqual(clone["project_id"], self.project.id)
        project = get_project(clone["project_id"])
        self.assertEqual(project.name, "Copied API")
        with closing(sqlite3.connect(project.directory / "data.sqlite")) as db:
            self.assertEqual(db.execute("SELECT value FROM records").fetchone(), ("saved",))
        self.assertIsNone(self.tool("get_worker_status", {"project_id": project.id})["pid"])

    def test_archive_rejects_overwrite_symlink_bad_zip_unconfirmed_and_stale_export(self):
        args = {**self.ids, "archive_path": str(self.root / "project.zip"), "expected_revision": self.revision, "confirm": True}
        self.tool_error("export_project_archive", {**args, "confirm": False}, "confirmation")
        self.assertFalse((self.root / "project.zip").exists())
        self.tool_error("export_project_archive", {**args, "expected_revision": "f" * 64}, "changed")
        self.assertFalse((self.root / "project.zip").exists())
        exported = self.export()
        self.tool_error("export_project_archive", args, "File exists")
        link = self.root / "link.zip"
        link.symlink_to(exported["archive_path"])
        result = self.rpc("tools/call", {"name": "inspect_project_archive", "arguments": {"archive_path": str(link)}})
        self.assertTrue(result["isError"])
        self.tool_error("export_project_archive", {**args, "archive_path": str(self.project.directory / "export.zip")}, "outside registered")
        bad = self.root / "invalid.zip"
        bad.write_bytes(b"invalid")
        self.tool_error("inspect_project_archive", {"archive_path": str(bad)}, "Not a readable")

    def test_workspace_export_stops_worker_and_secret_export_is_explicit(self):
        self.gateway.get(self.mock + "/hello")
        url = f"/api/projects/{self.project.id}/environment"
        self.console.put(url + "/JWT_SECRET", json={"expected_revision": self.console.get(url).json()["revision"], "value": "private-export-secret"})
        exported = self.export(kind="workspace")
        self.assertEqual(exported["worker_status"], "stopped")
        self.assertEqual(self.tool("get_worker_status", self.ids)["status"], "stopped")
        with zipfile.ZipFile(exported["archive_path"]) as archive:
            self.assertNotIn(".reqlica/environment.json", archive.namelist())
        private = self.export("private.zip", kind="workspace", include_secrets=True)
        self.assertTrue(private["private_environment_included"])
        self.assertNotIn("private-export-secret", json.dumps(private))
        self.tool_error("export_project_archive", {**self.ids, "archive_path": str(self.root / "bad.zip"),
            "expected_revision": self.revision, "include_secrets": True, "confirm": True}, "workspace")
        self.tool_error("import_project_archive", {"archive_path": private["archive_path"], "expected_sha256": private["sha256"],
            "directory": str(self.root / "restored"), "clone": True, "confirm": True}, "preserve identity")
        self.gateway.request("DELETE", f"/api/projects/{self.project.id}", json={"delete_files": False})
        restored = self.tool("import_project_archive", {"archive_path": private["archive_path"], "expected_sha256": private["sha256"],
            "directory": str(self.root / "restored"), "confirm": True})
        self.assertEqual(restored["project_id"], self.project.id)
        self.assertTrue(restored["private_environment_included"])

    def test_relocate_uses_directory_and_code_pins_and_leaves_worker_stopped(self):
        state = self.tool("get_worker_status", self.ids)
        args = {**self.ids, "expected_revision": state["active_revision"], "expected_directory": state["directory"],
            "directory": str(self.root / "relocated"), "confirm": True}
        self.tool_error("relocate_project", {**args, "expected_directory": str(self.root / "wrong")}, "changed")
        moved = self.tool("relocate_project", args)
        self.assertEqual(moved["status"], "stopped")
        self.assertFalse(self.project.directory.exists())
        self.assertEqual(str(get_project(self.project.id).directory), args["directory"])
        self.assertEqual(self.tool("get_worker_status", self.ids)["directory"], args["directory"])

    def test_project_ai_only_gets_project_scoped_reads_and_mutations_are_annotated(self):
        reads = {"get_traffic_policies", "get_worker_status", "get_data_baseline", "list_database_migrations"}
        writes = {"set_traffic_policy", "reset_scenario_counters", "control_project_worker", "capture_data_baseline",
                  "reset_project_data", "export_project_archive", "import_project_archive", "relocate_project"}
        self.assertTrue(reads <= agent.PROJECT_ONLY)
        run = {"draft_id": None, "allow_edits": True, "allow_execution": True}
        for name in reads:
            self.assertEqual(agent._arguments(self.project.id, run, name, {})["project_id"], self.project.id)
            with self.assertRaisesRegex(ValueError, "different project"):
                agent._arguments(self.project.id, run, name, {"project_id": self.other.id})
        self.assertFalse(writes & agent._allowed(run))
        tools = {item["name"]: item for item in self.rpc("tools/list")["tools"]}
        for name in reads:
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"])
        for name in writes:
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"])
            self.assertTrue(tools[name]["annotations"]["destructiveHint"])
            self.assertIn("confirm", tools[name]["inputSchema"]["required"])
        self.assertTrue(tools["control_project_worker"]["annotations"]["openWorldHint"])
