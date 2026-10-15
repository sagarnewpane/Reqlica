"""Fresh-session context discovers drafts and actual SQLite definitions, not records."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from reqlica.projects import drafts
from reqlica.projects.context import get_project_context
from reqlica.projects.store import create_project
from reqlica.projects.versions import (
    atomic_write_json,
    code_directory,
    content_digest,
    snapshot_code,
)


class ContextTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "project", "Shop")
        self.database = self.project.directory / "data.sqlite"

    def add_database(self):
        database = sqlite3.connect(self.database)
        with database:
            database.executescript("""
                PRAGMA user_version = 7;
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'closed'))
                );
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    body TEXT NOT NULL
                );
                CREATE INDEX posts_account ON posts(account_id);
                CREATE VIEW post_owners AS SELECT posts.id, accounts.email
                    FROM posts JOIN accounts ON accounts.id=posts.account_id;
                CREATE TRIGGER posts_touch AFTER INSERT ON posts BEGIN
                    UPDATE accounts SET status=status WHERE id=NEW.account_id;
                END;
            """)
            database.execute(
                "INSERT INTO accounts(email) VALUES (?)", ("PRIVATE_RECORD_EMAIL",)
            )
            database.execute(
                "INSERT INTO posts(account_id, body) VALUES (1, ?)",
                ("PRIVATE_RECORD_BODY",),
            )
        database.close()

    def test_database_structure_includes_keys_indexes_and_relationships_not_rows(self):
        self.add_database()
        result = get_project_context(self.project.id)
        schema = result["database"]
        self.assertEqual(schema["status"], "available", schema)
        self.assertFalse(schema["records_included"])
        self.assertEqual(schema["user_version"], 7)
        tables = {table["name"]: table for table in schema["tables"]}
        columns = {item["name"]: item for item in tables["accounts"]["columns"]}
        self.assertEqual(columns["id"]["pk"], 1)
        self.assertEqual(columns["email"]["notnull"], 1)
        self.assertEqual(columns["status"]["dflt_value"], "'active'")
        self.assertTrue(any(index["unique"] for index in tables["accounts"]["indexes"]))
        self.assertEqual(tables["posts"]["indexes"][0]["name"], "posts_account")
        self.assertEqual(schema["relationships"][0]["from_columns"], ["account_id"])
        self.assertEqual(schema["relationships"][0]["to_columns"], ["id"])
        self.assertEqual(schema["relationships"][0]["on_delete"], "CASCADE")
        self.assertEqual(schema["views"][0]["name"], "post_owners")
        self.assertEqual(schema["triggers"][0]["name"], "posts_touch")
        self.assertNotIn("PRIVATE_RECORD", json.dumps(result))
        self.assertEqual(result["handoff"]["runtime_health"], "not_queried")

    def test_database_fingerprint_changes_for_schema_not_record_writes(self):
        self.add_database()
        before = get_project_context(self.project.id)["database"]["fingerprint"]
        database = sqlite3.connect(self.database)
        with database:
            database.execute("UPDATE posts SET body='changed'")
        after = get_project_context(self.project.id)["database"]["fingerprint"]
        self.assertEqual(before, after)
        with database:
            database.execute("ALTER TABLE posts ADD COLUMN published INTEGER DEFAULT 0")
        changed = get_project_context(self.project.id)["database"]["fingerprint"]
        database.close()
        self.assertNotEqual(before, changed)

    def test_focused_context_omits_code_and_can_select_database_tables(self):
        self.add_database()
        context = get_project_context(
            self.project.id, include_source=False, database_tables=["posts"]
        )
        self.assertEqual(context["source"], {})
        self.assertEqual(context["source_status"], "omitted")
        self.assertIn("endpoints/hello.py", context["files"])
        self.assertEqual(
            [item["name"] for item in context["database"]["tables"]], ["posts"]
        )
        self.assertEqual(
            context["database"]["relationships"][0]["to_table"], "accounts"
        )
        self.assertTrue(context["database"]["triggers"])
        self.assertFalse(context["database"]["views"])
        selected = get_project_context(
            self.project.id,
            source_paths=["endpoints/hello.py"],
            include_database=False,
            include_drafts=False,
        )
        self.assertEqual(set(selected["source"]), {"endpoints/hello.py"})
        self.assertEqual(selected["database"]["status"], "not_requested")
        self.assertEqual(selected["draft_index"]["status"], "not_requested")
        with self.assertRaises(ValueError):
            get_project_context(self.project.id, source_paths=["../project.json"])
        with self.assertRaises(FileNotFoundError):
            get_project_context(self.project.id, source_paths=["missing.py"])

    def test_bound_table_names_cannot_execute_sql_and_unknown_tables_are_reported(self):
        self.add_database()
        name = "odd'\"; DROP TABLE accounts;--"
        database = sqlite3.connect(self.database)
        with database:
            database.execute(
                'CREATE TABLE "' + name.replace('"', '""') + '" (id INTEGER)'
            )
        database.close()
        result = get_project_context(self.project.id, database_tables=[name])
        self.assertEqual(result["database"]["status"], "available", result["database"])
        self.assertEqual(result["database"]["tables"][0]["name"], name)
        missing = get_project_context(self.project.id, database_tables=["missing"])
        self.assertEqual(missing["database"]["status"], "error")
        self.assertIn("Unknown database tables", missing["database"]["error"])
        self.assertIn(
            "accounts",
            {
                item["name"]
                for item in get_project_context(self.project.id)["database"]["tables"]
            },
        )

    def test_missing_corrupt_and_symlink_databases_do_not_hide_source(self):
        self.database.unlink()
        context = get_project_context(self.project.id)
        self.assertEqual(context["database"]["status"], "missing")
        self.assertFalse(self.database.exists())
        self.database.write_bytes(b"not a database")
        context = get_project_context(self.project.id)
        self.assertEqual(context["database"]["status"], "error")
        self.assertIn("endpoints/hello.py", context["source"])
        self.database.unlink()
        outside = self.root / "outside.sqlite"
        sqlite3.connect(outside).close()
        self.database.symlink_to(outside)
        context = get_project_context(self.project.id)
        self.assertEqual(context["database"]["status"], "error")
        self.assertIn("Symlink", context["database"]["error"])

    def test_committed_wal_schema_is_visible_and_truncation_is_explicit(self):
        database = sqlite3.connect(self.database)
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA wal_autocheckpoint=0")
            with database:
                database.execute("CREATE TABLE wal_table (id INTEGER PRIMARY KEY)")
            result = get_project_context(self.project.id)["database"]
            self.assertEqual(result["status"], "available", result)
            self.assertIn("wal_table", {table["name"] for table in result["tables"]})
        finally:
            database.close()
        self.add_database()
        with patch("reqlica.projects.database_schema.MAX_OBJECTS", 1):
            result = get_project_context(self.project.id)["database"]
        self.assertTrue(result["truncated"])
        self.assertEqual(result["limits"]["objects"], 1)
        self.assertGreater(result["object_count"], 1)

    def test_authored_notes_and_draft_context_do_not_replace_published_facts(self):
        draft = drafts.create_draft(self.project.id)
        draft = drafts.write_draft_file(
            self.project.id,
            draft["id"],
            "PROJECT_CONTEXT.md",
            "Business intent: orders reserve stock. Pending: add order tests.\n",
            draft["revision"],
        )
        revision = content_digest(drafts.draft_directory(self.project, draft["id"]))
        with patch(
            "reqlica.runtime.worker.build_app",
            side_effect=AssertionError("must not execute"),
        ):
            context = get_project_context(
                self.project.id, draft["id"], include_source=False
            )
        self.assertEqual(context["project_notes"]["status"], "present")
        self.assertIn("orders reserve stock", context["project_notes"]["content"])
        self.assertIn("Author-provided", context["project_notes"]["authority"])
        self.assertEqual(context["handoff"]["scope"], "draft")
        self.assertTrue(context["handoff"]["draft_base_is_current"])
        self.assertEqual(context["database"]["scope"], "current_live_database_schema")
        self.assertEqual(context["revision"], revision)
        self.assertEqual(
            get_project_context(self.project.id)["project_notes"]["status"], "missing"
        )

    def test_invalid_manifest_and_missing_schema_are_recoverable_context(self):
        draft = drafts.create_draft(self.project.id)
        drafts.write_draft_file(
            self.project.id, draft["id"], "manifest.json", "{broken", draft["revision"]
        )
        context = get_project_context(self.project.id, draft["id"])
        self.assertTrue(context["manifest_error"])
        self.assertEqual(context["endpoints"], [])
        self.assertEqual(context["source"]["manifest.json"], "{broken")
        self.assertEqual(context["api_schema"]["status"], "unavailable")


class DraftDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "project")

    def test_discovery_summaries_page_without_choosing_a_draft(self):
        ids = {drafts.create_draft(self.project.id)["id"] for _ in range(3)}
        first = drafts.list_drafts(self.project.id, summary=True, limit=2)
        second = drafts.list_drafts(
            self.project.id, summary=True, limit=2, offset=first["next_offset"]
        )
        self.assertEqual(
            {item["id"] for item in first["drafts"] + second["drafts"]}, ids
        )
        self.assertEqual(first["total"], 3)
        self.assertIsNone(second["next_offset"])
        self.assertTrue(all(item["progress"] == "editing" for item in first["drafts"]))
        self.assertTrue(all(item["base_is_current"] for item in first["drafts"]))
        context = get_project_context(self.project.id, include_source=False)
        self.assertEqual(context["draft_index"]["total"], 3)
        for limit, offset in ((0, 0), (101, 0), (1, -1)):
            with self.assertRaises(ValueError):
                drafts.list_drafts(
                    self.project.id, summary=True, limit=limit, offset=offset
                )

    def test_check_approval_and_stale_base_progress_are_derived_not_guessed(self):
        draft = drafts.create_draft(self.project.id)
        args = self.project.id, draft["id"], draft["revision"]
        self.assertEqual(drafts.validate_draft(*args)["status"], "passed")
        entry = drafts.list_drafts(self.project.id, summary=True)["drafts"][0]
        self.assertEqual(entry["progress"], "validated")
        self.assertFalse(entry["eligible_for_activation"])
        self.assertEqual(drafts.test_draft(*args)["status"], "passed")
        entry = drafts.list_drafts(self.project.id, summary=True)["drafts"][0]
        self.assertEqual(entry["progress"], "ready_for_review")
        self.assertFalse(entry["eligible_for_activation"])
        drafts.approve_draft(*args)
        entry = drafts.list_drafts(self.project.id, summary=True)["drafts"][0]
        self.assertEqual(entry["progress"], "approved")
        self.assertTrue(entry["eligible_for_activation"])
        self.assertNotIn("output", entry["validation"])
        self.assertNotIn("output", entry["test"])
        self.assertNotIn("warnings", entry["validation"]["schema"])
        context = get_project_context(self.project.id, draft["id"])
        self.assertEqual(context["api_schema"]["status"], "available")
        self.assertEqual(
            drafts.inspect_draft(*args[:2])["approval"], {"revision": args[2]}
        )
        version = uuid4().hex
        snapshot_code(
            code_directory(self.project), self.project.directory / "versions" / version
        )
        atomic_write_json(
            self.project.directory / "project.json",
            {
                "id": self.project.id,
                "name": self.project.name,
                "active_version": version,
            },
        )
        entry = drafts.list_drafts(self.project.id, summary=True)["drafts"][0]
        self.assertEqual(entry["progress"], "stale_base")
        self.assertFalse(entry["eligible_for_activation"])

    def test_failed_checks_and_direct_edits_are_reflected_in_discovery(self):
        draft = drafts.create_draft(self.project.id)
        changed = drafts.write_draft_file(
            self.project.id,
            draft["id"],
            "endpoints/hello.py",
            "def broken(:\n",
            draft["revision"],
        )
        result = drafts.validate_draft(
            self.project.id, draft["id"], changed["revision"]
        )
        self.assertEqual(result["status"], "failed", result)
        entry = drafts.list_drafts(self.project.id, summary=True)["drafts"][0]
        self.assertEqual(entry["progress"], "checks_failed")
        self.assertFalse(entry["eligible_for_activation"])
        self.assertNotIn("output", entry["validation"])
        handler = (
            drafts.draft_directory(self.project, draft["id"]) / "endpoints/hello.py"
        )
        handler.write_text("def hello():\n    return {'hello': 'world'}\n")
        entry = drafts.list_drafts(self.project.id, summary=True)["drafts"][0]
        self.assertNotEqual(entry["revision"], changed["revision"])
        self.assertEqual(entry["progress"], "editing")
        self.assertIsNone(entry["validation"])
        self.assertIsNone(entry["approval"])

    def test_unreadable_draft_state_does_not_hide_valid_drafts(self):
        valid = drafts.create_draft(self.project.id)
        bad_id = "f" * 32
        (self.project.directory / ".reqlica/drafts" / f"{bad_id}.json").write_text(
            "not JSON"
        )
        entries = {
            item["id"]: item
            for item in drafts.list_drafts(self.project.id, summary=True)["drafts"]
        }
        self.assertEqual(entries[valid["id"]]["progress"], "editing")
        self.assertEqual(entries[bad_id]["progress"], "unreadable")
        self.assertTrue(entries[bad_id]["error"])
