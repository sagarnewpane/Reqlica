"""Declarative schema plans, disposable checks, and confirmed additive live setup."""

import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation.errors import GenerationError
from reqlica.generation.schemas import GenerationRequest
from reqlica.generation.service import _validate_plan, generate_endpoints
from reqlica.projects import drafts
from reqlica.projects.context import get_project_context
from reqlica.projects.database_setup import SetupApply, apply_setup, preview_setup
from reqlica.projects.schema_plan import (
    SCHEMA_PLAN_PATH,
    parse_plan,
    require_database_ready,
    setup,
)
from reqlica.projects.store import Endpoint, create_project
from reqlica.projects.versions import code_directory, content_digest
from reqlica.runtime.worker import WorkerManager

PLAN = {
    "version": 1,
    "tables": [
        {
            "name": "users",
            "columns": [
                {
                    "name": "id",
                    "type": "INTEGER",
                    "primary_key": True,
                    "nullable": False,
                },
                {"name": "email", "type": "TEXT", "nullable": False, "unique": True},
                {"name": "password_hash", "type": "TEXT", "nullable": False},
                {"name": "created_at", "type": "TEXT", "nullable": False},
            ],
        }
    ],
}

HANDLER = """from pydantic import BaseModel
from reqlica.runtime.sdk import connection

class Count(BaseModel):
    count: int

def user_count() -> Count:
    with connection() as db:
        return Count(count=db.execute("SELECT COUNT(*) FROM users").fetchone()[0])
"""

TESTS = """import sqlite3
import unittest
from uuid import uuid4
from reqlica.runtime.sdk import connection, transaction

class DatabaseTests(unittest.TestCase):
    def test_account_storage_is_persistent_within_a_run(self):
        email = str(uuid4()) + "@example.com"
        with transaction() as db:
            db.execute("INSERT INTO users(email, password_hash, created_at) VALUES (?, ?, ?)",
                       (email, "test-only-hash", "2026-10-18T00:00:00Z"))
        with connection() as db:
            self.assertEqual(db.execute("SELECT password_hash FROM users WHERE email = ?", (email,)).fetchone()[0], "test-only-hash")

    def test_email_uniqueness(self):
        email = str(uuid4()) + "@example.com"
        with self.assertRaises(sqlite3.IntegrityError), transaction() as db:
            for _ in range(2):
                db.execute("INSERT INTO users(email, password_hash, created_at) VALUES (?, ?, ?)",
                           (email, "test-only-hash", "2026-10-18T00:00:00Z"))
"""


class DatabaseSetupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")})
        )
        self.project = create_project(self.root / "auth", "Auth setup test")
        self.path = self.project.directory / "data.sqlite"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE preserved(value TEXT)")
            db.execute("INSERT INTO preserved VALUES ('keep this record')")
        self.draft = drafts.create_draft(self.project.id)
        self.write(SCHEMA_PLAN_PATH, json.dumps(PLAN))
        self.write("endpoints/users.py", HANDLER)
        self.write("tests/test_database.py", TESTS)
        self.draft = drafts.upsert_endpoint(
            self.project.id,
            self.draft["id"],
            Endpoint(
                method="GET",
                path="/users/count",
                handler="endpoints/users.py:user_count",
            ),
            self.draft["revision"],
        )

    def args(self):
        return self.project.id, self.draft["id"], self.draft["revision"]

    def write(self, path, content):
        self.draft = drafts.write_draft_file(
            *self.args()[:2], path, content, self.draft["revision"]
        )

    def tables(self):
        with sqlite3.connect(self.path) as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            }

    def checks(self):
        validation = drafts.validate_draft(*self.args())
        self.assertEqual(validation["status"], "passed", validation)
        result = drafts.test_draft(*self.args())
        self.assertEqual(result["status"], "passed", result)
        self.assertGreaterEqual(result["tests_passed"], 3)

    def request(self, preview):
        return SetupApply(
            expected_revision=self.draft["revision"],
            expected_schema=preview["schema_fingerprint"],
            expected_preview_digest=preview["preview_digest"],
            confirm=True,
        )

    def test_preview_and_checks_never_create_live_tables_or_records(self):
        raw = self.path.read_bytes()
        preview = preview_setup(*self.args())
        self.assertEqual(preview["pending_tables"], ["users"])
        self.assertIn('CREATE TABLE "users"', preview["sql"][0])
        self.checks()
        result = drafts.call_draft_endpoint(*self.args(), "GET", "/users/count")
        self.assertEqual(result["status_code"], 200, result)
        self.assertEqual(result["body"], {"count": 0})
        self.assertNotIn("users", self.tables())
        self.assertEqual(self.path.read_bytes(), raw)

    def test_confirmed_apply_creates_tables_keeps_records_and_saves_prechange_backup(
        self,
    ):
        before = content_digest(code_directory(self.project))
        preview = preview_setup(*self.args())
        self.checks()
        result = apply_setup(self.project.id, self.draft["id"], self.request(preview))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["created_tables"], ["users"])
        self.assertIn("users", self.tables())
        backup = self.project.directory / result["backup"]
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as db:
            self.assertIsNone(
                db.execute(
                    "SELECT name FROM sqlite_schema WHERE name='users'"
                ).fetchone()
            )
            self.assertEqual(
                db.execute("SELECT value FROM preserved").fetchone()[0],
                "keep this record",
            )
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(
                db.execute("SELECT value FROM preserved").fetchone()[0],
                "keep this record",
            )
        self.assertEqual(content_digest(code_directory(self.project)), before)
        state = drafts.inspect_draft(*self.args()[:2])
        self.assertIsNone(state["approval"])
        fresh = preview_setup(*self.args())
        self.assertEqual(fresh["pending_tables"], [])
        again = apply_setup(self.project.id, self.draft["id"], self.request(fresh))
        self.assertEqual(again["status"], "already_applied")

    def test_setup_is_required_before_approved_code_can_activate(self):
        self.checks()
        drafts.approve_draft(*self.args())
        self.assertFalse(
            drafts.list_drafts(self.project.id, summary=True)["drafts"][0][
                "eligible_for_activation"
            ]
        )
        with self.assertRaisesRegex(ValueError, "Apply the reviewed"):
            drafts.require_approved(*self.args())
        preview = preview_setup(*self.args())
        apply_setup(self.project.id, self.draft["id"], self.request(preview))
        self.assertEqual(
            drafts.require_approved(*self.args())["revision"], self.draft["revision"]
        )
        require_database_ready(
            drafts.draft_directory(self.project, self.draft["id"]), self.path
        )
        self.assertTrue(
            drafts.list_drafts(self.project.id, summary=True)["drafts"][0][
                "eligible_for_activation"
            ]
        )

    def test_live_api_storage_survives_restart_after_explicit_setup_and_activation(
        self,
    ):
        source = (
            HANDLER
            + """
from reqlica.runtime.sdk import transaction

class NewUser(BaseModel):
    email: str

def create_user(body: NewUser) -> Count:
    with transaction() as db:
        db.execute("INSERT INTO users(email, password_hash, created_at) VALUES (?, ?, ?)",
                   (body.email, "test-only-hash", "2026-10-18T00:00:00Z"))
    return user_count()
"""
        )
        self.write("endpoints/users.py", source)
        self.draft = drafts.upsert_endpoint(
            self.project.id,
            self.draft["id"],
            Endpoint(
                method="POST", path="/users", handler="endpoints/users.py:create_user"
            ),
            self.draft["revision"],
        )
        self.checks()
        manager = WorkerManager()
        self.addCleanup(manager.close)
        drafts.approve_draft(*self.args())
        with self.assertRaisesRegex(ValueError, "Apply the reviewed"):
            manager.activate(*self.args())
        apply_setup(
            self.project.id, self.draft["id"], self.request(preview_setup(*self.args()))
        )
        manager.activate(*self.args())
        manager.start(self.project.id)
        worker = manager.get_or_start(self.project.id)
        url = f"http://127.0.0.1:{worker.port}"
        with httpx.Client(trust_env=False) as client:
            created = client.post(
                url + "/users", json={"email": "persistent@example.com"}
            )
            self.assertEqual(created.status_code, 200, created.text)
            self.assertEqual(created.json(), {"count": 1})
            manager.stop(self.project.id)
            manager.start(self.project.id)
            restarted = manager.get_or_start(self.project.id)
            result = client.get(f"http://127.0.0.1:{restarted.port}/users/count")
            self.assertEqual(result.json(), {"count": 1})

    def test_checks_revision_and_preview_digest_are_required(self):
        preview = preview_setup(*self.args())
        request = self.request(preview)
        with self.assertRaisesRegex(ValueError, "Validation and tests"):
            apply_setup(self.project.id, self.draft["id"], request)
        self.checks()
        with self.assertRaisesRegex(ValueError, "differs"):
            apply_setup(
                self.project.id,
                self.draft["id"],
                request.model_copy(update={"expected_preview_digest": "a" * 64}),
            )
        self.write("endpoints/note.py", "# newer draft\n")
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            apply_setup(self.project.id, self.draft["id"], request)
        self.assertNotIn("users", self.tables())

    def test_live_schema_drift_and_stale_base_are_rejected(self):
        preview = preview_setup(*self.args())
        self.checks()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE concurrent(id INTEGER)")
        with self.assertRaisesRegex(ValueError, "schema changed"):
            apply_setup(self.project.id, self.draft["id"], self.request(preview))
        stale = self.project.model_copy(update={"active_version": "f" * 32})
        with (
            patch("reqlica.projects.database_setup.get_project", return_value=stale),
            self.assertRaisesRegex(ValueError, "base is stale"),
        ):
            preview_setup(*self.args())
        self.assertNotIn("users", self.tables())

    def test_existing_objects_are_not_replaced_or_altered(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, password TEXT)")
            db.execute("INSERT INTO users VALUES (1, 'keep')")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            preview_setup(*self.args())
        validation = drafts.validate_draft(*self.args())
        self.assertEqual(validation["status"], "failed")
        with sqlite3.connect(self.path) as db:
            self.assertEqual(
                db.execute("SELECT password FROM users").fetchone()[0], "keep"
            )

    def test_failed_backup_leaves_schema_unchanged(self):
        preview = preview_setup(*self.args())
        self.checks()
        with (
            patch(
                "reqlica.projects.database_setup.backup_database",
                side_effect=OSError("Backup disk unavailable"),
            ),
            self.assertRaises(OSError),
        ):
            apply_setup(self.project.id, self.draft["id"], self.request(preview))
        self.assertNotIn("users", self.tables())

    def test_mid_application_failure_rolls_back_all_created_tables(self):
        preview = preview_setup(*self.args())
        self.checks()

        def interrupted(db, plan, *, create):
            result = setup(db, plan, create=create)
            if create:
                raise sqlite3.OperationalError("injected failure after table creation")
            return result

        with (
            patch("reqlica.projects.database_setup.setup", side_effect=interrupted),
            self.assertRaisesRegex(ValueError, "rolled back"),
        ):
            apply_setup(self.project.id, self.draft["id"], self.request(preview))
        self.assertNotIn("users", self.tables())

    def test_default_sql_text_is_quoted_as_a_literal_not_executed(self):
        plan = {
            "version": 1,
            "tables": [
                {
                    "name": "defaults",
                    "columns": [
                        {
                            "name": "value",
                            "type": "TEXT",
                            "default": "'); DROP TABLE preserved; --",
                        }
                    ],
                }
            ],
        }
        self.write(SCHEMA_PLAN_PATH, json.dumps(plan))
        preview = preview_setup(*self.args())
        self.assertEqual(preview["pending_tables"], ["defaults"])
        self.assertIn("preserved", self.tables())

    def test_metadata_limits_prevent_applying_uninspectable_schemas(self):
        preview = preview_setup(*self.args())
        self.checks()
        with patch("reqlica.projects.database_setup.MAX_OBJECTS", 1):
            with self.assertRaisesRegex(ValueError, "object limit"):
                preview_setup(*self.args())
            with self.assertRaisesRegex(ValueError, "object limit"):
                apply_setup(self.project.id, self.draft["id"], self.request(preview))
        self.assertNotIn("users", self.tables())
        oversized = {
            "version": 1,
            "tables": [
                {
                    "name": "large_table",
                    "columns": [
                        {"name": f"column{i}", "type": "TEXT", "default": "x" * 1000}
                        for i in range(20)
                    ],
                }
            ],
        }
        self.write(SCHEMA_PLAN_PATH, json.dumps(oversized))
        with self.assertRaisesRegex(ValueError, "16,384"):
            preview_setup(*self.args())

    def test_bad_references_fail_atomically_on_disposable_database(self):
        plan = copy.deepcopy(PLAN)
        plan["tables"].append(
            {
                "name": "sessions",
                "columns": [
                    {
                        "name": "user_id",
                        "type": "INTEGER",
                        "references": {"table": "missing", "column": "id"},
                    }
                ],
            }
        )
        self.write(SCHEMA_PLAN_PATH, json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "Missing referenced"):
            preview_setup(*self.args())
        self.assertNotIn("users", self.tables())

    def test_foreign_keys_and_unique_constraints_are_applied(self):
        plan = copy.deepcopy(PLAN)
        plan["tables"].append(
            {
                "name": "sessions",
                "columns": [
                    {
                        "name": "user_id",
                        "type": "INTEGER",
                        "nullable": False,
                        "references": {"table": "users", "column": "id"},
                    },
                    {"name": "token", "type": "TEXT"},
                ],
                "unique_together": [["user_id", "token"]],
            }
        )
        self.write(SCHEMA_PLAN_PATH, json.dumps(plan))
        self.checks()
        apply_setup(
            self.project.id, self.draft["id"], self.request(preview_setup(*self.args()))
        )
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO sessions VALUES (999, 'test')")

    def test_schema_plan_rejects_raw_sql_injection_and_destructive_operations(self):
        cases = [
            "DROP TABLE users",
            '{"version":1,"version":1,"tables":[]}',
            json.dumps({**PLAN, "sql": "DROP TABLE preserved"}),
        ]
        for field, value in (
            ("name", "users; DROP TABLE preserved"),
            ("type", "TEXT); DROP TABLE preserved; --"),
        ):
            plan = copy.deepcopy(PLAN)
            if field == "name":
                plan["tables"][0][field] = value
            else:
                plan["tables"][0]["columns"][0][field] = value
            cases.append(json.dumps(plan))
        for text in cases:
            with self.subTest(text=text[:80]), self.assertRaises(ValueError):
                parse_plan(text)
        self.assertIn("preserved", self.tables())

    def test_context_separates_planned_tables_from_live_database(self):
        context = get_project_context(
            self.project.id, self.draft["id"], include_source=False
        )
        self.assertEqual(
            context["database_setup"]["plan"]["tables"][0]["name"], "users"
        )
        self.assertNotIn("users", {t["name"] for t in context["database"]["tables"]})

    def test_http_confirm_and_revision_guards(self):
        self.checks()
        with TestClient(create_app()) as client:
            root = f"/api/projects/{self.project.id}/drafts/{self.draft['id']}/database"
            preview = client.post(
                root + "/preview", json={"expected_revision": self.draft["revision"]}
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            body = self.request(preview.json()).model_dump()
            for confirm in (False, 1, None):
                self.assertEqual(
                    client.post(
                        root + "/apply", json={**body, "confirm": confirm}
                    ).status_code,
                    422,
                )
            response = client.post(root + "/apply", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_generator_saves_a_plan_without_creating_live_tables(self):
        plan = {
            "summary": "Persistent account storage",
            "assumptions": [],
            "files": [
                {"path": SCHEMA_PLAN_PATH, "content": json.dumps(PLAN)},
                {"path": "endpoints/users.py", "content": HANDLER},
                {"path": "tests/test_database.py", "content": TESTS},
            ],
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/users/count",
                    "handler": "endpoints/users.py:user_count",
                }
            ],
        }
        with patch(
            "reqlica.generation.service.provider.complete",
            return_value=(json.dumps(plan), None),
        ) as complete:
            result = generate_endpoints(
                self.project.id,
                GenerationRequest(requirements="Persistent account storage"),
            )
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(result["database_setup"]["path"], SCHEMA_PLAN_PATH)
        self.assertNotIn("users", self.tables())
        complete.assert_called_once()
        saved = drafts.draft_directory(self.project, result["draft"]["id"])
        self.assertEqual(json.loads((saved / SCHEMA_PLAN_PATH).read_text()), PLAN)

    def test_generation_cannot_remove_or_change_a_versioned_table_declaration(self):
        context = get_project_context(self.project.id, self.draft["id"])
        changed = copy.deepcopy(PLAN)
        changed["tables"][0]["columns"].pop()
        proposal = {
            "summary": "Change user fields",
            "assumptions": [],
            "files": [
                {"path": SCHEMA_PLAN_PATH, "content": json.dumps(changed)},
                {"path": "tests/test_database.py", "content": TESTS},
            ],
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/users/count",
                    "handler": "endpoints/users.py:user_count",
                }
            ],
        }
        with self.assertRaisesRegex(GenerationError, "Preserve existing"):
            _validate_plan(json.dumps(proposal), context)
