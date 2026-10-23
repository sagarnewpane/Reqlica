"""Immutable SQLite baselines and confirmed data restore primitives."""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from reqlica.projects.database_setup import database_path
from reqlica.projects.drafts import _contained
from reqlica.projects.store import get_project
from reqlica.projects.versions import _fsync_directory, atomic_write_json, project_lock
from reqlica.runtime.activation import _schema
from reqlica.runtime.sdk.database import backup_database, restore_database

BASELINE_PATH = ".reqlica/data-baseline.json"
EMPTY_REVISION = "0" * 32


class ConfirmedRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{32}$")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class DataReset(ConfirmedRevision):
    expected_schema: str = Field(pattern=r"^[0-9a-f]{64}$")


class Baseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: str
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _fingerprint(path):
    return hashlib.sha256(json.dumps(_schema(path), separators=(",", ":")).encode()).hexdigest()


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _baseline(project):
    path = _contained(project.directory, BASELINE_PATH)
    if not path.exists():
        return None
    try:
        return Baseline.model_validate_json(path.read_bytes())
    except ValueError as error:
        raise ValueError("Invalid baseline metadata; inspect the local configuration") from error


def baseline_file(project, revision):
    return _contained(project.directory, f".reqlica/data-baselines/{revision}.sqlite")


def baseline_info(project_id):
    project = get_project(project_id)
    with project_lock(project.directory):
        baseline = _baseline(project)
        try:
            schema = _fingerprint(database_path(project))
        except sqlite3.Error as error:
            raise ValueError("Cannot inspect the current SQLite schema") from error
        return {"project_id":project.id, "revision":baseline.revision if baseline else EMPTY_REVISION,
                "baseline":baseline.model_dump() if baseline else None,
                "current_schema_fingerprint":schema}


def capture_baseline(project_id, body: ConfirmedRevision):
    project = get_project(project_id)
    with project_lock(project.directory):
        current = _baseline(project)
        if body.expected_revision != (current.revision if current else EMPTY_REVISION):
            raise ValueError("Baseline changed; inspect it again")
        revision = uuid4().hex
        path = baseline_file(project, revision)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=False)
        try:
            backup_database(database_path(project), path)
            baseline = Baseline(revision=revision, created_at=datetime.now(timezone.utc).isoformat(),
                                schema_fingerprint=_fingerprint(path), digest=_digest(path))
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            _fsync_directory(path.parent)
            atomic_write_json(_contained(project.directory, BASELINE_PATH), baseline.model_dump())
        except (sqlite3.Error, TimeoutError) as error:
            raise ValueError("Baseline snapshot failed; live data was not changed") from error
        return {"project_id":project.id, "revision":revision, "baseline":baseline.model_dump()}


def checked_baseline(project, body: DataReset):
    baseline = _baseline(project)
    if baseline is None or baseline.revision != body.expected_revision:
        raise ValueError("Baseline is missing or changed; inspect it again")
    path = baseline_file(project, baseline.revision)
    if not path.is_file() or _digest(path) != baseline.digest:
        raise ValueError("Baseline snapshot is missing or modified")
    try:
        live_schema = _fingerprint(database_path(project))
        if live_schema != body.expected_schema or live_schema != baseline.schema_fingerprint or _fingerprint(path) != live_schema:
            raise ValueError("Live and baseline schemas must match; inspect or capture a compatible baseline")
    except sqlite3.Error as error:
        raise ValueError("Cannot validate baseline and live database") from error
    return path


def restore_baseline(project, body: DataReset):
    """Caller holds project_lock/database_guard and has stopped/drained the worker."""
    source = checked_baseline(project, body)
    database = database_path(project)
    backup = _contained(project.directory, f".reqlica/database-backups/{uuid4().hex}.sqlite")
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.touch(mode=0o600, exist_ok=False)
    try:
        backup_database(database, backup)
        with backup.open("rb") as stream:
            os.fsync(stream.fileno())
        _fsync_directory(backup.parent)
        restore_database(source, database)
    except (sqlite3.Error, TimeoutError) as error:
        raise ValueError("Data restore failed; retain the pre-reset backup for recovery") from error
    return {"project_id":project.id, "status":"reset", "baseline_revision":body.expected_revision,
            "backup":str(backup.relative_to(project.directory)), "worker_status":"stopped"}
