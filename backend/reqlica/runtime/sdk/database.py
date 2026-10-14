"""Small, process-local SQLite API for project handlers."""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

_database_path: Path | None = None
BACKUP_TIMEOUT = 10.0


def configure_database(path: Path | str) -> None:
    """Select this worker process's database without opening it."""
    global _database_path
    _database_path = Path(path).expanduser().resolve()


def database_path() -> Path:
    if _database_path is None:
        raise RuntimeError("Project database has not been configured")
    return _database_path


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Open an autocommit connection; use transaction() for atomic writes."""
    with closing(sqlite3.connect(database_path(), isolation_level=None)) as database:
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        database.execute("PRAGMA busy_timeout = 5000")
        yield database


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Serialize writers and commit only if the complete operation succeeds."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        try:
            yield database
            database.commit()
        except BaseException:
            database.rollback()
            raise


def backup_database(source: Path | str, destination: Path | str) -> None:
    """Copy a consistent SQLite snapshot, including committed WAL contents."""
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if source == destination or (destination.exists() and source.samefile(destination)):
        raise ValueError("Database source and destination must be different")
    deadline = time.monotonic() + BACKUP_TIMEOUT

    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise TimeoutError("Database snapshot timed out")

    # Read-only mode must not silently create a database for a missing source.
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(destination)) as copy,
    ):
        original.backup(copy, pages=128, progress=progress)


def restore_database(backup: Path | str, destination: Path | str) -> None:
    """Restore through SQLite; callers must stop destination workers first."""
    backup_database(backup, destination)
