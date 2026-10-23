"""Data-only generation, disposable previews, and atomic append-only imports."""

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
    DataImportRequest,
    DataValidationRequest,
)
from reqlica.generation.errors import GenerationError
from reqlica.projects.mock_data import (
    database_schema,
    insert_data,
    selected_table,
    validate_data,
    validate_records,
)
from reqlica.projects.store import create_project


def rows():
    return [
        {"title": "First", "price": 10, "author_id": 1},
        {"title": "Second", "price": 20, "author_id": 1},
    ]


def proposal(records=None):
    return {
        "summary": "Two synthetic books",
        "assumptions": ["Author 1 was supplied as a field hint."],
        "records_json": json.dumps(rows() if records is None else records),
    }


class MockDataTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "books", "Books")
        self.database = self.project.directory / "data.sqlite"
        with sqlite3.connect(self.database) as db:
            db.executescript("""
                CREATE TABLE authors(id INTEGER PRIMARY KEY, name TEXT);
                INSERT INTO authors VALUES(1, 'private-live-author-name');
                CREATE TABLE books(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL UNIQUE ON CONFLICT REPLACE,
                    price REAL NOT NULL CHECK(price >= 0),
                    author_id INTEGER REFERENCES authors(id)
                );
            """)

    def batch(self, records=None):
        return DataValidationRequest(
            target="sqlite",
            table="books",
            records=rows() if records is None else records,
            expected_schema=database_schema(self.project.id)["fingerprint"],
        )

    def import_request(self, batch, preview):
        return DataImportRequest(
            **batch.model_dump(),
            expected_preview_digest=preview["preview_digest"],
            confirm=True,
        )

    def contents(self):
        with sqlite3.connect(self.database) as db:
            return db.execute("SELECT title, price FROM books ORDER BY id").fetchall()



    def test_disposable_validation_then_explicit_atomic_insert(self):
        batch = self.batch()
        raw = self.database.read_bytes()
        preview = validate_data(self.project.id, batch)
        self.assertEqual(preview["validation"]["status"], "passed")
        self.assertEqual(self.database.read_bytes(), raw)
        result = insert_data(self.project.id, self.import_request(batch, preview))
        self.assertEqual(result["inserted_count"], 2)
        self.assertEqual(len(result["row_ids"]), 2)
        self.assertEqual(self.contents(), [("First", 10), ("Second", 20)])

    def test_foreign_key_check_failure_keeps_editable_preview_and_live_data_unchanged(
        self,
    ):
        bad = [{**row, "author_id": 999} for row in rows()]
        preview = validate_data(self.project.id, self.batch(bad))
        self.assertEqual(preview["records"], bad)
        self.assertEqual(preview["validation"]["status"], "failed")
        self.assertEqual(self.contents(), [])

    def test_late_constraint_failure_rolls_back_all_rows_and_never_replaces_existing(
        self,
    ):
        batch = self.batch()
        preview = validate_data(self.project.id, batch)
        with sqlite3.connect(self.database) as db:
            db.execute(
                "INSERT INTO books(title, price, author_id) VALUES('Second', 99, 1)"
            )
        with self.assertRaisesRegex(GenerationError, "entire batch was rolled back"):
            insert_data(self.project.id, self.import_request(batch, preview))
        self.assertEqual(self.contents(), [("Second", 99)])

    def test_changed_rows_or_schema_cannot_reuse_the_review_digest(self):
        batch = self.batch()
        preview = validate_data(self.project.id, batch)
        changed = batch.model_copy(update={"records": [{**rows()[0], "price": 100}]})
        with self.assertRaisesRegex(ValueError, "differ from the reviewed"):
            insert_data(self.project.id, self.import_request(changed, preview))
        with sqlite3.connect(self.database) as db:
            db.execute("ALTER TABLE books ADD COLUMN note TEXT")
        with self.assertRaisesRegex(ValueError, "schema changed"):
            insert_data(self.project.id, self.import_request(batch, preview))
        self.assertEqual(self.contents(), [])

    def test_no_unknown_columns_nested_sql_values_or_unsafe_table_names(self):
        for record in (
            {"unknown": 1},
            {"title": {"nested": "not SQL text"}, "price": 10},
        ):
            with self.subTest(record=record), self.assertRaises(GenerationError):
                validate_data(self.project.id, self.batch([record]))
        batch = self.batch().model_copy(
            update={"table": 'books"; DROP TABLE authors;--'}
        )
        with self.assertRaises(GenerationError):
            validate_data(self.project.id, batch)
        self.assertEqual(self.contents(), [])

    def test_triggers_virtual_and_generated_columns_are_not_import_targets(self):
        with sqlite3.connect(self.database) as db:
            db.execute(
                "CREATE TRIGGER unsafe_insert AFTER INSERT ON books BEGIN DELETE FROM authors; END"
            )
        with self.assertRaisesRegex(GenerationError, "triggers"):
            validate_data(self.project.id, self.batch())
        schema = database_schema(self.project.id)
        schema["tables"].append(
            {"name": "virtual", "sql": "CREATE VIRTUAL TABLE virtual USING fts5(value)"}
        )
        with self.assertRaisesRegex(GenerationError, "Virtual"):
            selected_table(schema, "virtual")
        with sqlite3.connect(self.database) as db:
            db.execute(
                "CREATE TABLE computed(value INTEGER, doubled INTEGER GENERATED ALWAYS AS (value * 2))"
            )
        batch = DataValidationRequest(
            target="sqlite",
            table="computed",
            expected_schema=database_schema(self.project.id)["fingerprint"],
            records=[{"value": 1, "doubled": 999}],
        )
        with self.assertRaisesRegex(GenerationError, "generated columns"):
            validate_data(self.project.id, batch)


    def test_record_limits_and_nonfinite_values_are_enforced(self):
        for records in (
            [],
            [{}],
            [{"n": float("inf")}],
            [{"n": 2**64}],
            [{"n": "x" * 8193}],
            [{"n": 1}] * 101,
        ):
            with (
                self.subTest(records=str(records)[:80]),
                self.assertRaises(GenerationError),
            ):
                validate_records(records)

    def test_deferred_foreign_keys_are_checked_in_disposable_previews(self):
        with sqlite3.connect(self.database) as db:
            db.execute(
                "CREATE TABLE deferred_books(title TEXT, author_id INTEGER "
                "REFERENCES authors(id) DEFERRABLE INITIALLY DEFERRED)"
            )
        batch = DataValidationRequest(
            target="sqlite",
            table="deferred_books",
            expected_schema=database_schema(self.project.id)["fingerprint"],
            records=[{"title": "New book", "author_id": 999}],
        )
        result = validate_data(self.project.id, batch)
        self.assertEqual(result["validation"]["status"], "failed")
        with sqlite3.connect(self.database) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM deferred_books").fetchone()[0], 0
            )


    def test_sql_like_values_and_quoted_identifiers_are_bound_as_data(self):
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE "odd""table" ("odd""column" TEXT)')
        batch = DataValidationRequest(
            target="sqlite",
            table='odd"table',
            expected_schema=database_schema(self.project.id)["fingerprint"],
            records=[{'odd"column': "'); DROP TABLE authors; --"}],
        )
        preview = validate_data(self.project.id, batch)
        insert_data(self.project.id, self.import_request(batch, preview))
        with sqlite3.connect(self.database) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM authors").fetchone()[0], 1
            )
            self.assertEqual(
                db.execute('SELECT "odd""column" FROM "odd""table"').fetchone()[0],
                "'); DROP TABLE authors; --",
            )


    def test_http_preview_validate_and_confirmed_insert_keep_credentials_out_of_data(
        self,
    ):
        with TestClient(create_app()) as client:
            root = f"/api/projects/{self.project.id}/data"
            schema = client.get(root + "/schema")
            self.assertEqual(schema.status_code, 200)
            self.assertNotIn("path", schema.json())
            batch = self.batch()
            reviewed = client.post(root + "/validate", json=batch.model_dump()).json()
            body = self.import_request(batch, reviewed).model_dump()
            for confirm in (False, 1, None):
                self.assertEqual(
                    client.post(
                        root + "/insert", json={**body, "confirm": confirm}
                    ).status_code,
                    422,
                )
            inserted = client.post(root + "/insert", json=body)
            self.assertEqual(inserted.status_code, 200, inserted.text)
            self.assertEqual(inserted.json()["inserted_count"], 2)
