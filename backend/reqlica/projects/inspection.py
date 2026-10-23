"""Bounded, project-local inspection events; never store request/response payloads."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from reqlica.projects.drafts import _contained
from reqlica.projects.store import get_project
from reqlica.projects.versions import _checked_directory

HISTORY_PATH = ".reqlica/request-history.sqlite"
MAX_EVENTS = 1000
EventKind = Literal["request", "runtime_error", "startup_error", "stdout", "stderr"]


def _path(directory):
    for suffix in ("", "-journal", "-wal", "-shm"):
        _contained(directory, HISTORY_PATH + suffix)
    return _contained(directory, HISTORY_PATH)


def record_event(directory, event):
    """Best effort: inspection failures must never change application responses."""
    try:
        payload = json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), **event}, allow_nan=False)
        if len(payload.encode()) > 8192:
            return
        _checked_directory(directory)
        state = _contained(directory, ".reqlica")
        state.mkdir(exist_ok=True)
        _checked_directory(state)
        path = _path(directory)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with closing(sqlite3.connect(path, timeout=0.25)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, request_id TEXT, payload TEXT NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS events_request ON events(request_id, id)")
            connection.execute("INSERT INTO events(kind, request_id, payload) VALUES(?, ?, ?)",
                               (event["kind"], event.get("request_id"), payload))
            connection.execute("DELETE FROM events WHERE id <= (SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?)", (MAX_EVENTS,))
    except (OSError, ValueError, sqlite3.Error):
        return


def record_project_request(project_id, event, request_body=None, response_body=None):
    try:
        project = get_project(project_id)
        from reqlica.projects.capture import capture_request
        capture_request(project.directory, event, request_body, response_body)
        record_event(project.directory, event)
    except (OSError, ValueError):
        pass


def exception_details(error, code_directory):
    """Exception class and project frame locations; no messages, source or locals."""
    frames = []
    traceback = error.__traceback__
    while traceback is not None:
        filename = traceback.tb_frame.f_code.co_filename
        try:
            relative = Path(filename).resolve().relative_to(code_directory.resolve())
        except (OSError, ValueError):
            pass
        else:
            frames.append({"file": str(relative)[:256], "line": traceback.tb_lineno,
                           "function": traceback.tb_frame.f_code.co_name[:128]})
        traceback = traceback.tb_next
    return {"exception_type": type(error).__name__[:128], "frames": frames[-12:]}


def read_logs(project_id, limit=50, before_id=None, kind=None, request_id=None, event_id=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Use a limit of 1–100")
    if before_id is not None and (type(before_id) is not int or before_id < 1):
        raise ValueError("before_id must be a positive event ID")
    if kind not in (None, "request", "runtime_error", "startup_error", "stdout", "stderr"):
        raise ValueError("Unknown event kind")
    project = get_project(project_id)
    path = _path(project.directory)
    if not path.exists():
        if event_id is not None:
            raise FileNotFoundError("Inspection event not found")
        return {"project_id": project_id, "events": [], "next_before_id": None}
    clauses, values = [], []
    for column, value, operator in (("id", before_id, "<"), ("kind", kind, "="),
                                    ("request_id", request_id, "="), ("id", event_id, "=")):
        if value is not None:
            clauses.append(f"{column} {operator} ?")
            values.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as connection:
            # A newly created history file can briefly have no committed schema yet.
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone():
                rows = []
            else:
                rows = connection.execute("SELECT id, payload FROM events" + where + " ORDER BY id DESC LIMIT ?", (*values, limit + 1)).fetchall()
        entries = [{"id": row[0], **json.loads(row[1])} for row in rows[:limit]]
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise ValueError("Inspection history is unavailable or corrupt") from error
    if event_id is not None:
        if not entries:
            raise FileNotFoundError("Inspection event not found")
        return entries[0]
    return {"project_id": project_id, "events": entries,
            "next_before_id": entries[-1]["id"] if len(rows) > limit else None}
