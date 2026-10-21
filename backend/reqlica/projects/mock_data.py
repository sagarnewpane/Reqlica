"""Preview data on disposable SQLite copies; append reviewed records atomically."""

import hashlib
import json
import math
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from reqlica.generation.data_schemas import DataImportRequest, DataValidationRequest
from reqlica.generation.errors import GenerationError
from reqlica.projects.database_schema import read_database_schema
from reqlica.projects.data_contracts import application_data_contract, check_application_row, DataContractError, require_mockable
from reqlica.projects.drafts import _contained
from reqlica.projects.store import get_project
from reqlica.projects.versions import project_lock
from reqlica.runtime.sdk.database import backup_database

MAX_DATA_BYTES = 128 * 1024


def validate_records(records: object) -> list[dict]:
    def check(value, depth=0):
        if depth > 6:
            raise ValueError("Records may be nested at most six levels")
        if value is None or type(value) is bool:
            return
        if type(value) is int:
            if not -(2**63) <= value < 2**63:
                raise ValueError("Integers must fit in signed 64 bits")
        elif type(value) is float:
            if not math.isfinite(value):
                raise ValueError("Non-finite numbers are not supported")
        elif isinstance(value, str):
            if len(value.encode("utf-8")) > 8192:
                raise ValueError("Individual strings must fit within 8 KiB")
        elif isinstance(value, dict):
            if len(value) > 50 or any(
                not isinstance(k, str) or not k or len(k) > 128 for k in value
            ):
                raise ValueError(
                    "Use at most 50 named fields per object, with names up to 128 characters"
                )
            for item in value.values():
                check(item, depth + 1)
        elif isinstance(value, list):
            if len(value) > 100:
                raise ValueError("Nested arrays may have at most 100 items")
            for item in value:
                check(item, depth + 1)
        else:
            raise ValueError("Only JSON data values are allowed")

    try:
        if (
            not isinstance(records, list)
            or not 1 <= len(records) <= 100
            or any(not isinstance(row, dict) or not row for row in records)
        ):
            raise ValueError("Provide an array of 1–100 nonempty record objects")
        if (
            len(json.dumps(records, allow_nan=False, ensure_ascii=False).encode())
            > MAX_DATA_BYTES
        ):
            raise ValueError("The record batch exceeds 128 KiB")
        for row in records:
            check(row)
    except (ValueError, RecursionError) as error:
        raise GenerationError(
            str(error)
            if not isinstance(error, RecursionError)
            else "Records are too deeply nested",
            422,
        ) from error
    return records


def database_schema(project_id: str) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        result = read_database_schema(project)
        if result["status"] != "available" or result["truncated"]:
            raise GenerationError(
                "Database definitions are unavailable or incomplete; use JSON fixtures instead",
                409,
            )
        try:
            with closing(
                sqlite3.connect(
                    _database_path(project).as_uri() + "?mode=ro", uri=True, timeout=1
                )
            ) as database:
                kinds = dict(
                    database.execute(
                        "SELECT name, type FROM pragma_table_list WHERE schema = 'main'"
                    )
                )
            for table in result["tables"]:
                table["storage_type"] = kinds.get(table["name"])
        except sqlite3.Error as error:
            raise GenerationError(
                "SQLite table metadata is unavailable; use JSON fixtures instead", 409
            ) from error
    return {key: value for key, value in result.items() if key not in {"path", "error"}}


def selected_table(schema: dict, name: str) -> dict:
    table = next((table for table in schema["tables"] if table["name"] == name), None)
    if table is None:
        raise GenerationError(
            "Select an existing database table; no tables are created by this feature",
            409,
        )
    if (
        table.get("storage_type") not in (None, "table")
        or not table.get("sql")
        or re.match(r"\s*CREATE\s+VIRTUAL\s+TABLE\b", table["sql"], re.IGNORECASE)
    ):
        raise GenerationError(
            "Virtual/system tables cannot be seeded; use JSON fixtures instead", 422
        )
    if any(
        trigger["tbl_name"].casefold() == name.casefold()
        for trigger in schema["triggers"]
    ):
        raise GenerationError(
            "Tables with triggers are not supported by the append-only importer; use JSON fixtures or a reviewed endpoint",
            422,
        )
    return table


def _database_path(project) -> Path:
    path = _contained(project.directory, "data.sqlite")
    for suffix in ("-wal", "-shm", "-journal"):
        _contained(project.directory, "data.sqlite" + suffix)
    if not path.is_file():
        raise GenerationError("Project database is missing; it was not created", 409)
    return path


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _insert(database, table: dict, records: list[dict], contract=None) -> list[int | None]:
    kind = database.execute(
        "SELECT type FROM pragma_table_list WHERE schema = 'main' AND name = ?",
        (table["name"],),
    ).fetchone()
    if kind is None or kind[0] != "table":
        raise GenerationError(
            "Only ordinary SQLite tables can be seeded; virtual and shadow tables are excluded",
            422,
        )
    writable = {column["name"] for column in table["columns"] if not column["hidden"]}
    ids = []
    for index, row in enumerate(records):
        if not set(row) <= writable:
            raise GenerationError(
                "A record has unknown or generated columns; edit the preview and validate again",
                422,
            )
        if any(isinstance(value, (dict, list)) for value in row.values()):
            raise GenerationError(
                "SQLite values must be scalars; encode JSON columns as strings", 422
            )
        columns = list(row)
        # OR ABORT overrides schema-level REPLACE/IGNORE conflict policies.
        cursor = database.execute(
            f"INSERT OR ABORT INTO {_quote(table['name'])} ({','.join(_quote(c) for c in columns)}) VALUES ({','.join('?' for _ in columns)}) RETURNING *",
            [row[column] for column in columns],
        )
        stored = dict(zip((column[0] for column in cursor.description), cursor.fetchone()))
        if contract is not None:
            check_application_row(contract, table["name"], stored, index)
        ids.append(
            cursor.lastrowid
            if not re.search(r"\bWITHOUT\s+ROWID\b", table["sql"], re.IGNORECASE)
            else None
        )
        cursor.close()
    return ids


def _connect(path: Path):
    database = sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None
    )
    database.execute("PRAGMA foreign_keys = ON")
    database.execute("PRAGMA trusted_schema = OFF")
    deadline = time.monotonic() + 10
    database.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return database


def preview_digest(
    target: str, table: str | None, records: list[dict], fingerprint: str | None, application_revision: str | None = None
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "target": target,
                "table": table,
                "records": records,
                "schema": fingerprint,
                "application_revision": application_revision,
            },
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def validate_data(project_id: str, request: DataValidationRequest) -> dict:
    project = get_project(project_id)
    records = validate_records(request.records)
    fingerprint = None
    contract = None
    validation = {"status": "passed", "message": "JSON records are valid"}
    if request.target == "sqlite":
        with project_lock(project.directory):
            schema = database_schema(project_id)
            if schema["fingerprint"] != request.expected_schema:
                raise ValueError(
                    "Database schema changed; reload its definitions and validate again"
                )
            table = selected_table(schema, request.table)
            contract = application_data_contract(project_id, schema)
            require_mockable(contract, [request.table])
            fingerprint = schema["fingerprint"]
            with tempfile.TemporaryDirectory(prefix="reqlica-data-preview-") as folder:
                copied = Path(folder) / "preview.sqlite"
                backup_database(_database_path(project), copied)
                try:
                    with closing(_connect(copied)) as database:
                        database.execute("BEGIN IMMEDIATE")
                        _insert(database, table, records, contract)
                        # Commit only the disposable copy, so deferred foreign
                        # keys are checked exactly as they will be on insertion.
                        database.commit()
                    validation = {
                        "status": "passed",
                        "message": "Batch passed database and declared application field checks on a disposable copy",
                    }
                except DataContractError as error:
                    validation = {"status": "failed", "message": str(error), "issues": error.issues}
                except sqlite3.IntegrityError:
                    validation = {
                        "status": "failed",
                        "message": "Batch violates a UNIQUE, CHECK, NOT NULL, or foreign-key constraint. Edit the records or supply valid related IDs, then validate again.",
                    }
                except sqlite3.Error:
                    validation = {
                        "status": "failed",
                        "message": "SQLite could not validate this batch on the disposable copy. No live records were changed.",
                    }
            if database_schema(project_id)["fingerprint"] != fingerprint:
                raise ValueError(
                    "Database schema changed while preparing the preview; reload it"
                )
    return {
        "status": "preview",
        "target": request.target,
        "table": request.table,
        "records": records,
        "schema_fingerprint": fingerprint,
        "application_revision": contract["revision"] if contract else None,
        "application_checks": contract["coverage"] if contract else None,
        "preview_digest": preview_digest(
            request.target, request.table, records, fingerprint, contract["revision"] if contract else None
        ),
        "validation": validation,
    }


def insert_data(project_id: str, request: DataImportRequest) -> dict:
    project = get_project(project_id)
    records = validate_records(request.records)
    with project_lock(project.directory):
        schema = database_schema(project_id)
        contract = application_data_contract(project_id, schema)
        require_mockable(contract, [request.table])
        digest = preview_digest(request.target, request.table, records, request.expected_schema, contract["revision"])
        if digest != request.expected_preview_digest:
            raise ValueError("The records differ from the reviewed preview or the application changed; validate them again")
        try:
            with closing(_connect(_database_path(project))) as database:
                database.execute("BEGIN IMMEDIATE")
                schema = database_schema(project_id)
                if schema["fingerprint"] != request.expected_schema:
                    raise ValueError("Database schema changed; nothing was inserted")
                table = selected_table(schema, request.table)
                try:
                    ids = _insert(database, table, records, contract)
                    database.commit()
                except BaseException:
                    database.rollback()
                    raise
        except DataContractError as error:
            raise GenerationError(str(error) + ": " + error.issues[0]["message"] + "; nothing was inserted", 422) from error
        except sqlite3.IntegrityError as error:
            raise GenerationError(
                "The batch conflicts with current database constraints. The entire batch was rolled back; no records were inserted.",
                409,
            ) from error
        except sqlite3.Error as error:
            raise GenerationError(
                "SQLite could not insert the batch; no automatic retry was made", 409
            ) from error
    return {
        "status": "inserted",
        "table": request.table,
        "inserted_count": len(records),
        "row_ids": ids,
        "preview_digest": digest,
    }
