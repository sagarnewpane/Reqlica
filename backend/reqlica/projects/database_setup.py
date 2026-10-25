"""Revision-pinned database setup previews and confirmed, backed-up application."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from reqlica.projects import drafts
from reqlica.projects.database_schema import MAX_OBJECTS, read_database_schema
from reqlica.projects.schema_plan import (
    SCHEMA_PLAN_PATH,
    load_plan,
    prepare_database,
    setup,
    statements,
)
from reqlica.projects.store import get_project
from reqlica.projects.versions import _fsync_directory, project_lock
from reqlica.runtime.sdk.database import backup_database


class SetupRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class SetupApply(SetupRevision):
    expected_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


def database_path(project) -> Path:
    path = drafts._contained(project.directory, "data.sqlite")
    for suffix in ("-wal", "-shm", "-journal"):
        drafts._contained(project.directory, "data.sqlite" + suffix)
    if not path.is_file():
        raise ValueError(
            "Project database is missing; restore or initialize it explicitly"
        )
    return path


def _schema(project) -> dict:
    schema = read_database_schema(project)
    if schema["status"] != "available" or schema["truncated"]:
        raise ValueError(
            "Complete current database definitions are required for schema setup"
        )
    return schema


def digest(
    project_id: str, draft_id: str, revision: str, fingerprint: str, sql: list[str]
) -> str:
    return hashlib.sha256(
        json.dumps([project_id, draft_id, revision, fingerprint, sql]).encode()
    ).hexdigest()


def _state(project, draft_id, revision):
    state = drafts._state(project, draft_id, revision)
    if state["base_version"] != project.active_version:
        raise ValueError(
            "Draft base is stale; database setup requires the active base version"
        )
    return state


def preview_setup(project_id: str, draft_id: str, expected_revision: str) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        _state(project, draft_id, expected_revision)
        directory = drafts.draft_directory(project, draft_id)
        plan = load_plan(directory)
        schema = _schema(project)
        sql = [sql for _, sql in statements(plan)] if plan else []
        if plan:
            with tempfile.TemporaryDirectory(
                prefix="reqlica-schema-preview-"
            ) as folder:
                copied = Path(folder) / "data.sqlite"
                backup_database(database_path(project), copied)
                try:
                    pending = prepare_database(directory, copied)
                except sqlite3.Error as error:
                    raise ValueError(
                        "Database setup could not be prepared on the disposable copy; check relationships and constraints"
                    ) from error
        else:
            pending = []
        if schema["object_count"] + len(pending) > MAX_OBJECTS:
            raise ValueError(
                "Schema setup would exceed the database metadata object limit"
            )
        if _schema(project)["fingerprint"] != schema["fingerprint"]:
            raise ValueError("Database schema changed during preview; preview again")
        _state(project, draft_id, expected_revision)
        return {
            "status": "preview",
            "draft_id": draft_id,
            "revision": expected_revision,
            "path": SCHEMA_PLAN_PATH,
            "plan": plan.model_dump() if plan else None,
            "pending_tables": pending,
            "sql": sql,
            "schema_fingerprint": schema["fingerprint"],
            "preview_digest": digest(
                project_id, draft_id, expected_revision, schema["fingerprint"], sql
            ),
            "validation": "passed",
            "live_changes": False,
        }


def apply_setup(project_id: str, draft_id: str, request: SetupApply) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        state = _state(project, draft_id, request.expected_revision)
        drafts._require_checks(state, project)
        directory = drafts.draft_directory(project, draft_id)
        plan = load_plan(directory)
        if plan is None:
            raise ValueError("This draft has no database schema plan")
        sql = [sql for _, sql in statements(plan)]
        if (
            digest(
                project_id, draft_id, state["revision"], request.expected_schema, sql
            )
            != request.expected_preview_digest
        ):
            raise ValueError(
                "Schema plan differs from the reviewed preview; preview again"
            )
        path = database_path(project)
        try:
            with closing(
                sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
            ) as db:
                db.execute("PRAGMA foreign_keys = ON")
                db.execute("PRAGMA trusted_schema = OFF")
                db.execute("BEGIN IMMEDIATE")
                try:
                    schema = _schema(project)
                    if schema["fingerprint"] != request.expected_schema:
                        raise ValueError(
                            "Database schema changed; preview again before applying"
                        )
                    pending = setup(db, plan, create=False)
                    if schema["object_count"] + len(pending) > MAX_OBJECTS:
                        raise ValueError(
                            "Schema setup would exceed the database metadata object limit"
                        )
                    if not pending:
                        return {
                            "status": "already_applied",
                            "revision": state["revision"],
                            "created_tables": [],
                            "backup": None,
                        }
                    backup = drafts._contained(
                        project.directory,
                        f".reqlica/database-backups/{uuid4().hex}.sqlite",
                    )
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.touch(mode=0o600, exist_ok=False)
                    backup_database(path, backup)
                    with backup.open("rb") as stream:
                        os.fsync(stream.fileno())
                    _fsync_directory(backup.parent)
                    setup(db, plan, create=True)
                    _state(project, draft_id, request.expected_revision)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        except sqlite3.Error as error:
            raise ValueError(
                "Database setup failed; the schema transaction was rolled back"
            ) from error
        return {
            "status": "applied",
            "revision": state["revision"],
            "created_tables": pending,
            "backup": str(backup.relative_to(project.directory)),
        }
