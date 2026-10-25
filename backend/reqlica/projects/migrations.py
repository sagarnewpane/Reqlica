"""Reviewed declarative schema changes, prepared on copies before live restore."""

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from reqlica.projects import drafts
from reqlica.projects.database_setup import database_path
from reqlica.projects.reset import ConfirmedRevision, _fingerprint, _digest
from reqlica.projects.schema_plan import Column, Table, SchemaPlan, quote, statements
from reqlica.projects.store import get_project
from reqlica.projects.versions import _fsync_directory, atomic_write_json, code_directory, content_digest, project_lock
from reqlica.runtime.sdk.database import backup_database, restore_database

MIGRATION_PATH = "database/migration.json"


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add_column", "drop_column", "rename_column", "rename_table", "drop_table", "rebuild_table"]
    table: str
    name: str | None = None
    new_name: str | None = None
    column: Column | None = None
    definition: Table | None = None
    copy_columns: dict[str, str] = Field(default_factory=dict, max_length=50)


class MigrationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    from_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    operations: list[Operation] = Field(min_length=1, max_length=50)


class MigrationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    operations: list[Operation] = Field(min_length=1, max_length=50)


class MigrationApply(ConfirmedRevision):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class MigrationRollback(ConfirmedRevision):
    expected_schema: str = Field(pattern=r"^[0-9a-f]{64}$")


class MigrationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    phase: Literal["prepared", "applied", "rolled_back", "aborted"]
    backup: str
    backup_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    from_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    pre_rollback_backup: str | None = None


def _receipt(project, identity):
    path = drafts._contained(project.directory, f".reqlica/migrations/{identity}.json")
    record = MigrationReceipt.model_validate_json(path.read_bytes()).model_dump(exclude_none=True)
    if record["id"] != identity:
        raise ValueError("Migration receipt identity mismatch")
    return path, record


def plan_digest(plan):
    return hashlib.sha256(plan.model_dump_json().encode()).hexdigest()


def load_migration(directory):
    path = drafts._contained(directory, MIGRATION_PATH)
    if not path.exists():
        return None
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("Migration plan exceeds 64 KiB")
    return MigrationPlan.model_validate_json(raw)


def compile_operations(operations):
    sql = []
    for item in operations:
        table = quote(item.table)
        if item.op == "drop_table":
            sql.append(f"DROP TABLE {table}")
        elif item.op == "rename_table":
            sql.append(f"ALTER TABLE {table} RENAME TO {quote(item.new_name or '')}")
        elif item.op == "drop_column":
            sql.append(f"ALTER TABLE {table} DROP COLUMN {quote(item.name or '')}")
        elif item.op == "rename_column":
            sql.append(f"ALTER TABLE {table} RENAME COLUMN {quote(item.name or '')} TO {quote(item.new_name or '')}")
        elif item.op == "add_column":
            if item.column is None or item.column.primary_key or item.column.unique:
                raise ValueError("Add-column requires a non-primary, non-unique column; use rebuild for constraints")
            declaration = SchemaPlan(version=1, tables=[Table(name=item.table, columns=[item.column])])
            definition = statements(declaration)[0][1].split(" (", 1)[1][:-1]
            sql.append(f"ALTER TABLE {table} ADD COLUMN {definition}")
        else:
            if item.definition is None or item.definition.name != item.table or not item.copy_columns:
                raise ValueError("Rebuild requires a matching table definition and explicit destination-to-source column mapping")
            allowed = {c.name for c in item.definition.columns}
            if not set(item.copy_columns) <= allowed:
                raise ValueError("Unknown rebuild destination column")
            temporary = "reqlica_rebuild_" + uuid4().hex[:12]
            definition = item.definition.model_copy(update={"name":temporary})
            declaration = SchemaPlan(version=1, tables=[definition])
            sql.append(statements(declaration)[0][1])
            target_columns = ", ".join(quote(c) for c in item.copy_columns)
            source_columns = ", ".join(quote(c) for c in item.copy_columns.values())
            sql.extend([f"INSERT INTO {quote(temporary)} ({target_columns}) SELECT {source_columns} FROM {table}",
                        f"DROP TABLE {table}", f"ALTER TABLE {quote(temporary)} RENAME TO {table}"])
    return sql


def execute_operations(database, operations):
    sql = compile_operations(operations)
    try:
        with closing(sqlite3.connect(database)) as db:
            deadline = time.monotonic() + 30
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN IMMEDIATE")
            try:
                for statement in sql:
                    db.execute(statement)
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("Migration violates foreign keys")
                tables = [row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:
                    for reference in db.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,)):
                        if reference[2] not in tables:
                            raise ValueError("Migration leaves a missing referenced table")
                db.commit()
            except BaseException:
                db.rollback()
                raise
            db.execute("PRAGMA foreign_keys=ON")
            for table in tables:
                # Detect malformed FK targets even when no records exist.
                escaped = '"' + table.replace('"', '""') + '"'
                db.execute(f"EXPLAIN INSERT INTO {escaped} DEFAULT VALUES").fetchall()
                db.execute(f"EXPLAIN DELETE FROM {escaped}").fetchall()
                writable = db.execute("SELECT name FROM pragma_table_xinfo(?) WHERE hidden=0 LIMIT 1", (table,)).fetchone()
                if writable:
                    column = '"' + writable[0].replace('"', '""') + '"'
                    db.execute(f"EXPLAIN UPDATE {escaped} SET {column}={column}").fetchall()
            for (view,) in db.execute("SELECT name FROM sqlite_schema WHERE type='view'"):
                escaped = '"' + view.replace('"', '""') + '"'
                db.execute(f"EXPLAIN SELECT * FROM {escaped}").fetchall()
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Migration integrity check failed")
    except sqlite3.Error as error:
        raise ValueError("Migration SQL or constraint validation failed") from error
    return sql


def prepare_migration(directory, database):
    plan = load_migration(directory)
    if plan is None:
        return
    current = _fingerprint(database)
    if current == plan.target_schema and plan.target_schema != plan.from_schema:
        return
    if current != plan.from_schema:
        raise ValueError("Migration source schema differs from the database")
    execute_operations(database, plan.operations)
    if _fingerprint(database) != plan.target_schema or plan.target_schema == plan.from_schema:
        raise ValueError("Migration target schema differs from its reviewed preview")


def preview_migration(project_id, body):
    project = get_project(project_id)
    with project_lock(project.directory), tempfile.TemporaryDirectory(prefix="reqlica-migration-") as folder:
        source = database_path(project)
        if _fingerprint(source) != body.expected_schema:
            raise ValueError("Database schema changed; inspect it again")
        copied = Path(folder).resolve() / "data.sqlite"
        backup_database(source, copied)
        before = list(sqlite_schema(copied))
        sql = execute_operations(copied, body.operations)
        target = _fingerprint(copied)
        if target == body.expected_schema:
            raise ValueError("Migration must change the schema; use data operations for data-only changes")
        plan = MigrationPlan(id=uuid4().hex, from_schema=body.expected_schema, target_schema=target, operations=body.operations)
        after = list(sqlite_schema(copied))
        return {"plan":plan.model_dump(mode="json"), "plan_digest":plan_digest(plan), "sql":sql,
                "removed_objects":[row for row in before if row not in after], "target_schema":target,
                "current_code_revision":content_digest(code_directory(project)), "live_changes":False}


def sqlite_schema(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def migration_history(project_id, *, limit=None, offset=0):
    project = get_project(project_id)
    with project_lock(project.directory):
        root = drafts._contained(project.directory, ".reqlica/migrations")
        paths = sorted(root.glob("*.json"))
        if limit is not None and (not 1 <= limit <= 100 or offset < 0):
            raise ValueError("Invalid migration history page")
        selected = paths if limit is None else paths[offset:offset + limit]
        rows = []
        for path in selected:
            checked = drafts._contained(project.directory, str(path.relative_to(project.directory)))
            if checked.stat().st_size > 65536:
                raise ValueError("Migration receipt exceeds 64 KiB")
            row = json.loads(checked.read_text())
            if not isinstance(row, dict):
                raise ValueError("Invalid migration receipt")
            rows.append(row)
        result = {"migrations": rows}
        if limit is not None:
            result.update(total=len(paths), next_offset=offset + limit if offset + limit < len(paths) else None)
        return result


def check_recovery(project, body):
    receipt, record = _receipt(project, body.expected_revision)
    current = _fingerprint(database_path(project))
    if record.get("id") != body.expected_revision or record.get("phase") != "prepared" or current != body.expected_schema:
        raise ValueError("Receipt/schema is not eligible for recovery")
    if current == record["target_schema"]:
        phase = "applied"
    elif current == record["from_schema"]:
        phase = "aborted"
    else:
        raise ValueError("Schema matches neither migration source nor target")
    return receipt, record, phase


def recover_migration(project, body):
    receipt, record, phase = check_recovery(project, body)
    atomic_write_json(receipt, {**record, "phase":phase})
    return {"id":record["id"], "phase":phase, "worker_status":"stopped"}


def check_migration(project, body):
    state = drafts._state(project, body.draft_id, body.expected_revision)
    if state["base_version"] != project.active_version:
        raise ValueError("Migration draft must use the current active base")
    drafts._require_checks(state, project)
    directory = drafts.draft_directory(project, body.draft_id)
    plan = load_migration(directory)
    if plan is None or plan_digest(plan) != body.expected_plan_digest or body.expected_schema != plan.from_schema:
        raise ValueError("Migration plan differs from the reviewed preview")
    if _fingerprint(database_path(project)) != plan.from_schema:
        raise ValueError("Live schema changed; preview a new migration")
    receipt = drafts._contained(project.directory, f".reqlica/migrations/{plan.id}.json")
    if receipt.exists():
        raise ValueError("This migration ID already has a receipt")
    return plan, directory, receipt


def apply_migration(project, body):
    plan, directory, receipt = check_migration(project, body)
    with tempfile.TemporaryDirectory(prefix="reqlica-live-migration-") as folder:
        prepared = Path(folder).resolve() / "data.sqlite"
        backup_database(database_path(project), prepared)
        prepare_migration(directory, prepared)
        if content_digest(directory) != body.expected_revision or _fingerprint(prepared) != plan.target_schema:
            raise ValueError("Draft/target changed while preparing migration")
        backup = drafts._contained(project.directory, f".reqlica/database-backups/migration-{plan.id}.sqlite")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.touch(mode=0o600, exist_ok=False)
        backup_database(database_path(project), backup)
        with backup.open("rb") as stream:
            os.fsync(stream.fileno())
        _fsync_directory(backup.parent)
        record = {"id":plan.id, "phase":"prepared", "backup":str(backup.relative_to(project.directory)),
                  "backup_digest":_digest(backup),
                  "from_schema":plan.from_schema, "target_schema":plan.target_schema,
                  "draft_id":body.draft_id, "revision":body.expected_revision, "plan_digest":plan_digest(plan)}
        receipt.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(receipt, record)
        restore_database(prepared, database_path(project))
        atomic_write_json(receipt, {**record, "phase":"applied"})
        return {**record, "phase":"applied", "worker_status":"stopped"}


def check_rollback(project, body):
    receipt, record = _receipt(project, body.expected_revision)
    if record.get("id") != body.expected_revision or record.get("phase") not in {"applied", "prepared"}:
        raise ValueError("Migration is not eligible for rollback")
    if _fingerprint(database_path(project)) != body.expected_schema or body.expected_schema != record["target_schema"]:
        raise ValueError("Current schema is not this migration's target")
    backup = drafts._contained(project.directory, record["backup"])
    if _fingerprint(backup) != record["from_schema"] or _digest(backup) != record["backup_digest"]:
        raise ValueError("Rollback snapshot schema mismatch")
    return record, receipt, backup


def rollback_migration(project, body):
    record, receipt, backup = check_rollback(project, body)
    safety = drafts._contained(project.directory, f".reqlica/database-backups/pre-rollback-{uuid4().hex}.sqlite")
    backup_database(database_path(project), safety)
    safety.chmod(0o600)
    with safety.open("rb") as stream:
        os.fsync(stream.fileno())
    _fsync_directory(safety.parent)
    restore_database(backup, database_path(project))
    atomic_write_json(receipt, {**record, "phase":"rolled_back", "pre_rollback_backup":str(safety.relative_to(project.directory))})
    return {"status":"rolled_back", "backup":str(safety.relative_to(project.directory)), "worker_status":"stopped"}
