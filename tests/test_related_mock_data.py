"""One prompt creates related rows, with actual IDs and all-table rollback."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation.data_schemas import (
    RelatedDataImportRequest, RelatedDataValidationRequest,
    RelatedMockDataRequest, RelatedMockDataPlan,
)
from reqlica.generation.errors import GenerationError
from reqlica.generation.mock_data import generate_related_mock_data
from reqlica.projects.mock_data import database_schema
from reqlica.projects.related_data import (
    validate_related_data, insert_related_data, validate_related_records,
)
from reqlica.projects.store import create_project


def records():
    # Child table deliberately comes first. IDs belong to existing data too.
    return {
        "posts": [
            {"user_id": {"$ref": "users.0.id"}, "title": "First post"},
            {"user_id": {"$ref": "users.1.id"}, "title": "Second post"},
        ],
        "users": [{"name": "Ava"}, {"name": "Milo"}],
    }


class RelatedDataTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")}))
        self.project = create_project(self.root / "social", "Social")
        self.path = self.project.directory / "data.sqlite"
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
                INSERT INTO users(id,name) VALUES(100,'private-existing-name');
                CREATE TABLE posts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), title TEXT NOT NULL UNIQUE);
            ''')
        self.schema = database_schema(self.project.id)["fingerprint"]

    def request(self, data=None):
        return RelatedDataValidationRequest(expected_schema=self.schema, records_by_table=records() if data is None else data)

    def import_request(self, request, preview):
        return RelatedDataImportRequest(**request.model_dump(), expected_preview_digest=preview["preview_digest"], confirm=True)

    def counts(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM posts)").fetchone()

    def test_two_users_and_two_posts_link_to_actual_ids_and_preview_never_writes(self):
        request = self.request()
        before = self.path.read_bytes()
        preview = validate_related_data(self.project.id, request)
        self.assertEqual(preview["validation"]["status"], "passed", preview)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([row["table"] for row in preview["insertion_order"]], ["users", "users", "posts", "posts"])
        # A concurrent write changes actual IDs after preview. References must resolve again.
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO users(id,name) VALUES(200,'another-existing-name')")
        result = insert_related_data(self.project.id, self.import_request(request, preview))
        self.assertEqual(result["inserted_counts"], {"users": 2, "posts": 2})
        self.assertEqual(result["primary_keys"]["users"], [{"id": 201}, {"id": 202}])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT u.name,p.title FROM posts p JOIN users u ON u.id=p.user_id ORDER BY p.id").fetchall(), [("Ava", "First post"), ("Milo", "Second post")])

    def test_late_child_conflict_rolls_back_parents_and_other_children(self):
        request = self.request()
        preview = validate_related_data(self.project.id, request)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO posts(user_id,title) VALUES(100,'Second post')")
        with self.assertRaisesRegex(GenerationError, "All tables were rolled back"):
            insert_related_data(self.project.id, self.import_request(request, preview))
        self.assertEqual(self.counts(), (1, 1))

    def test_edit_or_schema_change_requires_new_preview(self):
        request = self.request()
        preview = validate_related_data(self.project.id, request)
        changed = records()
        changed["users"][0]["name"] = "Edited"
        with self.assertRaisesRegex(ValueError, "differ from the reviewed"):
            insert_related_data(self.project.id, self.import_request(self.request(changed), preview))
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE users ADD COLUMN note TEXT")
        with self.assertRaisesRegex(ValueError, "schema changed"):
            insert_related_data(self.project.id, self.import_request(request, preview))
        self.assertEqual(self.counts(), (1, 0))

    def test_invalid_references_and_unknown_tables_fail_without_writes(self):
        for reference in ("users.9.id", "users.0.missing", "missing.0.id", "bad", "users.-1.id"):
            data = records()
            data["posts"][0]["user_id"] = {"$ref": reference}
            with self.subTest(reference=reference), self.assertRaises(GenerationError):
                validate_related_data(self.project.id, self.request(data))
        data = records()
        data["posts"][0]["title"] = {"$ref": "users.0.name"}
        with self.assertRaisesRegex(GenerationError, "declared foreign key"):
            validate_related_data(self.project.id, self.request(data))
        with self.assertRaises(GenerationError):
            validate_related_data(self.project.id, self.request({"nonexistent": [{"name": "A"}]}))
        self.assertEqual(self.counts(), (1, 0))

    def test_cycles_are_rejected_but_self_table_dependencies_are_ordered(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE categories(id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER REFERENCES categories(id))")
        self.schema = database_schema(self.project.id)["fingerprint"]
        data = {"categories": [{"name": "Child", "parent_id": {"$ref": "categories.1.id"}}, {"name": "Parent"}]}
        preview = validate_related_data(self.project.id, self.request(data))
        self.assertEqual(preview["insertion_order"], [{"table": "categories", "row": 1}, {"table": "categories", "row": 0}])
        data["categories"][1]["parent_id"] = {"$ref": "categories.0.id"}
        with self.assertRaisesRegex(GenerationError, "cyclic"):
            validate_related_data(self.project.id, self.request(data))

    def test_explicit_parent_ids_insert_first_and_sqlite_constraints_are_checked(self):
        data = {"posts": [{"user_id": 800, "title": "One"}], "users": [{"id": 800, "name": "Parent"}]}
        preview = validate_related_data(self.project.id, self.request(data))
        self.assertEqual(preview["validation"]["status"], "passed")
        self.assertEqual(preview["insertion_order"][0]["table"], "users")
        data["users"][0]["name"] = "private-existing-name"
        preview = validate_related_data(self.project.id, self.request(data))
        self.assertEqual(preview["validation"]["status"], "failed")
        self.assertEqual(self.counts(), (1, 0))

    def test_single_prompt_generates_all_tables_and_never_sends_existing_rows(self):
        request = RelatedMockDataRequest(requirements="Create two users and two posts, one for each user", expected_schema=self.schema)
        output = {"summary": "Two users and two related posts", "assumptions": [], "records_by_table_json": json.dumps(records())}
        with patch("reqlica.generation.mock_data.provider.complete", return_value=(json.dumps(output), {})) as complete:
            result = generate_related_mock_data(self.project.id, request)
        complete.assert_called_once()
        self.assertIs(complete.call_args.kwargs["response_model"], RelatedMockDataPlan)
        self.assertNotIn("private-existing-name", json.dumps(complete.call_args.args[0]))
        self.assertEqual(result["counts"], {"users": 2, "posts": 2})
        self.assertEqual(self.counts(), (1, 0))

    def test_api_generate_returns_one_reviewable_dataset_without_writes(self):
        output = {"summary": "Related users and posts", "assumptions": [], "records_by_table_json": json.dumps(records())}
        with patch("reqlica.generation.mock_data.provider.complete", return_value=(json.dumps(output), {})) as complete, TestClient(create_app()) as client:
            response = client.post(f"/api/projects/{self.project.id}/data/related/generate", json={"requirements": "Create 2 users and 2 posts", "expected_schema": self.schema})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["counts"], {"users": 2, "posts": 2})
        complete.assert_called_once()
        self.assertEqual(self.counts(), (1, 0))

    def test_invalid_model_output_does_not_write_records(self):
        request = RelatedMockDataRequest(requirements="Two users and posts", expected_schema=self.schema)
        for raw in ("not JSON", json.dumps({"summary": "Bad", "assumptions": [], "records_by_table_json": "[]"})):
            with self.subTest(raw=raw), patch("reqlica.generation.mock_data.provider.complete", return_value=(raw, {})), self.assertRaises(GenerationError):
                generate_related_mock_data(self.project.id, request)
        self.assertEqual(self.counts(), (1, 0))

    def test_api_preview_insert_and_explicit_confirmation(self):
        with TestClient(create_app()) as client:
            root = f"/api/projects/{self.project.id}/data/related"
            body = self.request().model_dump()
            preview = client.post(root + "/validate", json=body)
            self.assertEqual(preview.status_code, 200, preview.text)
            insert = {**body, "expected_preview_digest": preview.json()["preview_digest"]}
            self.assertEqual(client.post(root + "/insert", json=insert).status_code, 422)
            self.assertEqual(client.post(root + "/insert", json={**insert, "confirm": True}).status_code, 200)
        self.assertEqual(self.counts(), (3, 2))

    def test_batch_limits_and_data_only_values(self):
        for data in ({}, {"users": [{"name": "A"}] * 101}, {"users": [{"name": "A"}] * 60, "posts": [{"title": "B"}] * 50}):
            with self.subTest(count=len(data)), self.assertRaises(GenerationError):
                validate_related_records(data)
        data = records()
        data["users"][0]["name"] = {"code": "not a reference"}
        with self.assertRaises(GenerationError):
            validate_related_data(self.project.id, self.request(data))
