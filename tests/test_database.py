"""Exercise the public SQLite SDK without running project workers."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from reqlica.runtime.sdk import (
    backup_database,
    configure_database,
    connection,
    database_path,
    restore_database,
    transaction,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "data.sqlite"
        self.enterContext(patch("reqlica.runtime.sdk.database._database_path", None))
        configure_database(self.path)

    def test_requires_configuration_and_freezes_relative_path(self):
        with patch("reqlica.runtime.sdk.database._database_path", None):
            with self.assertRaisesRegex(RuntimeError, "not been configured"):
                database_path()
            with self.assertRaises(RuntimeError), connection():
                self.fail("An unconfigured connection was opened")
        configure_database(Path("relative.sqlite"))
        self.assertEqual(database_path(), Path.cwd() / "relative.sqlite")
        self.assertFalse((Path.cwd() / "relative.sqlite").exists())

    def test_autocommit_rows_foreign_keys_and_close(self):
        with connection() as database:
            self.assertIsNone(database.isolation_level)
            self.assertEqual(
                database.execute("PRAGMA busy_timeout").fetchone()[0], 5000
            )
            self.assertEqual(database.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            database.execute("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
            database.execute(
                "CREATE TABLE children (parent INTEGER REFERENCES parents(id))"
            )
            database.execute("INSERT INTO parents VALUES (7)")
            with self.assertRaises(sqlite3.IntegrityError):
                database.execute("INSERT INTO children VALUES (8)")
        with self.assertRaises(sqlite3.ProgrammingError):
            database.execute("SELECT 1")
        with connection() as reopened:
            row = reopened.execute("SELECT id FROM parents").fetchone()
            self.assertIsInstance(row, sqlite3.Row)
            self.assertEqual(row["id"], 7)

    def test_connection_closes_on_error_but_autocommit_persists(self):
        with self.assertRaisesRegex(ValueError, "failure"), connection() as database:
            database.execute("CREATE TABLE records (value TEXT)")
            database.execute("INSERT INTO records VALUES ('saved')")
            raise ValueError("failure")
        with self.assertRaises(sqlite3.ProgrammingError):
            database.execute("SELECT 1")
        with connection() as reopened:
            self.assertEqual(
                reopened.execute("SELECT value FROM records").fetchone()[0], "saved"
            )

    def test_transaction_commit_rollback_and_close(self):
        with transaction() as database:
            self.assertTrue(database.in_transaction)
            database.execute("CREATE TABLE records (value TEXT)")
            database.execute("INSERT INTO records VALUES ('saved')")
        with self.assertRaisesRegex(ValueError, "failure"), transaction() as failed:
            failed.execute("INSERT INTO records VALUES ('discarded')")
            failed.execute("CREATE TABLE discarded (value TEXT)")
            raise ValueError("failure")
        with connection() as reopened:
            self.assertEqual(
                [row[0] for row in reopened.execute("SELECT value FROM records")],
                ["saved"],
            )
            self.assertIsNone(
                reopened.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'discarded'"
                ).fetchone()
            )
        for closed in (database, failed):
            with self.assertRaises(sqlite3.ProgrammingError):
                closed.execute("SELECT 1")

    def test_explicit_transaction_on_connection(self):
        with connection() as database:
            database.execute("CREATE TABLE records (value TEXT)")
            database.execute("BEGIN")
            database.execute("INSERT INTO records VALUES ('not committed')")
        with connection() as reopened:
            self.assertEqual(
                reopened.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0
            )

    def test_concurrent_writers_serialize_without_lost_updates(self):
        with connection() as database:
            database.execute("CREATE TABLE counter (value INTEGER)")
            database.execute("INSERT INTO counter VALUES (0)")
        barrier = threading.Barrier(4)

        def increment():
            barrier.wait(timeout=5)
            for _ in range(10):
                with transaction() as database:
                    value = database.execute("SELECT value FROM counter").fetchone()[0]
                    database.execute("UPDATE counter SET value = ?", (value + 1,))

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: increment(), range(4)))
        with connection() as database:
            self.assertEqual(
                database.execute("SELECT value FROM counter").fetchone()[0], 40
            )

    def test_open_connection_keeps_its_project_when_configuration_changes(self):
        with connection() as first:
            first.execute("CREATE TABLE records (value TEXT)")
            first.execute("INSERT INTO records VALUES ('first')")
            configure_database(self.root / "other.sqlite")
            with connection() as second:
                second.execute("CREATE TABLE records (value TEXT)")
                second.execute("INSERT INTO records VALUES ('second')")
                self.assertEqual(
                    first.execute("SELECT value FROM records").fetchone()[0], "first"
                )
        configure_database(self.path)
        with connection() as first:
            self.assertEqual(
                first.execute("SELECT value FROM records").fetchone()[0], "first"
            )

    def test_persists_and_configuration_is_process_local(self):
        script = """
import sys
from reqlica.runtime.sdk import configure_database, database_path, transaction
try:
    database_path()
except RuntimeError:
    pass
else:
    raise AssertionError('Configuration leaked into another process')
configure_database(sys.argv[1])
with transaction() as database:
    database.execute('CREATE TABLE records (value TEXT)')
    database.execute("INSERT INTO records VALUES ('from process')")
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(database_path(), self.path)
        with connection() as database:
            self.assertEqual(
                database.execute("SELECT value FROM records").fetchone()[0],
                "from process",
            )

    def test_backup_restore_includes_wal_and_does_not_copy_uncommitted_data(self):
        backup = self.root / "backup.sqlite"
        with connection() as database:
            self.assertEqual(
                database.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal"
            )
            database.execute("CREATE TABLE records (value TEXT)")
            database.execute("INSERT INTO records VALUES ('saved')")
            self.assertTrue(Path(str(self.path) + "-wal").exists())
            with transaction() as writer:
                writer.execute("INSERT INTO records VALUES ('pending')")
                backup_database(self.path, backup)
            database.execute("INSERT INTO records VALUES ('later')")
        restore_database(backup, self.path)
        with connection() as database:
            self.assertEqual(
                [row[0] for row in database.execute("SELECT value FROM records")],
                ["saved"],
            )
            self.assertEqual(
                database.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        self.assertEqual(backup.read_bytes()[:16], b"SQLite format 3\x00")

    def test_backup_rejects_missing_source_and_same_file(self):
        with self.assertRaises(sqlite3.OperationalError):
            backup_database(self.root / "missing.sqlite", self.root / "backup.sqlite")
        self.assertFalse((self.root / "missing.sqlite").exists())
        self.assertFalse((self.root / "backup.sqlite").exists())
        with connection() as database:
            database.execute("CREATE TABLE records (value TEXT)")
        for operation in (backup_database, restore_database):
            with self.assertRaises(ValueError):
                operation(self.path, self.path)
        alias = self.root / "alias.sqlite"
        os.link(self.path, alias)
        with self.assertRaises(ValueError):
            backup_database(self.path, alias)

    def test_backup_is_bounded_when_the_source_is_exclusively_locked(self):
        with connection() as database:
            database.execute("CREATE TABLE records (value TEXT)")
            database.execute("BEGIN EXCLUSIVE")
            with (
                patch(
                    "reqlica.runtime.sdk.database.time.monotonic", side_effect=[0, 11]
                ),
                self.assertRaisesRegex(TimeoutError, "snapshot timed out"),
            ):
                backup_database(self.path, self.root / "backup.sqlite")
            database.rollback()
            self.assertEqual(
                database.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
