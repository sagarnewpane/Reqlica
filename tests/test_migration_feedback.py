"""Migration failures identify repairable causes without exposing row values."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from reqlica.generation.agent import _tool_error_detail
from reqlica.projects.migrations import Operation, execute_operations


class MigrationFeedbackTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(directory) / "data.sqlite"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO records VALUES(1, 'private-row-value')")

    def rebuild(self, **changes):
        return Operation.model_validate({"op": "rebuild_table", "table": "records",
            "definition": {"name": "records", "columns": [
                {"name": "id", "type": "INTEGER", "primary_key": True},
                {"name": "value", "type": "TEXT"}]},
            "copy_columns": {"id": "id", "value": "value"}, **changes})

    def assert_rejected_without_change(self, operations, expected):
        before = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, expected) as caught:
            execute_operations(self.database, operations)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertNotIn("private-row-value", str(caught.exception))
        return str(caught.exception)

    def test_missing_live_table_is_not_a_generic_sql_error(self):
        operation = Operation(op="drop_table", table="draft_only_table")
        message = self.assert_rejected_without_change([operation], "does not exist in the live database")
        self.assertIn("database/schema.json", _tool_error_detail({"error": message}))

    def test_missing_source_column_cannot_be_copied_as_a_sqlite_string_literal(self):
        message = self.assert_rejected_without_change(
            [self.rebuild(copy_columns={"id": "id", "value": "new_column"})], "missing source column")
        self.assertIn("only existing source columns", _tool_error_detail({"error": message}))

    def test_text_cannot_be_copied_into_an_integer_primary_key(self):
        message = self.assert_rejected_without_change(
            [self.rebuild(copy_columns={"id": "value", "value": "value"})], "Migration datatype mismatch")
        self.assertIn("target column types", _tool_error_detail({"error": message}))

    def test_new_required_column_needs_data_or_a_default(self):
        operation = self.rebuild()
        operation.definition.columns[1].nullable = False
        operation.copy_columns = {"id": "id"}
        message = self.assert_rejected_without_change([operation], "Migration violates NOT NULL")
        self.assertIn("non-null value", _tool_error_detail({"error": message}))

    def test_mapping_is_validated_against_prior_operations_in_the_same_plan(self):
        rename = Operation(op="rename_column", table="records", name="value", new_name="renamed")
        execute_operations(self.database, [rename, self.rebuild(copy_columns={"id": "ID", "value": "RENAMED"})])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT * FROM records").fetchall(), [(1, "private-row-value")])

    def test_integer_primary_key_limitation_has_specific_ui_feedback(self):
        feedback = _tool_error_detail({"error": "1 validation error for SchemaPlan: This setup version supports only an INTEGER primary key"})
        self.assertIn("INTEGER primary keys only", feedback)
        self.assertNotIn("input schema", feedback)
