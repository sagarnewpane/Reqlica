"""Related mock rows: resolve generated IDs and append all tables atomically."""

import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from reqlica.generation.errors import GenerationError
from reqlica.projects.mock_data import (
    MAX_DATA_BYTES,
    _connect,
    _database_path,
    _quote,
    database_schema,
    selected_table,
    validate_records,
)
from reqlica.projects.data_contracts import application_data_contract, check_application_row, DataContractError, require_mockable
from reqlica.projects.store import get_project
from reqlica.projects.versions import project_lock
from reqlica.runtime.sdk.database import backup_database


def validate_related_records(records_by_table):
    if not isinstance(records_by_table, dict) or not 1 <= len(records_by_table) <= 10:
        raise GenerationError("Provide records for 1–10 existing tables", 422)
    total = 0
    for name, records in records_by_table.items():
        if not isinstance(name, str) or not name or len(name) > 128:
            raise GenerationError("Invalid table name", 422)
        validate_records(records)
        total += len(records)
    if total > 100 or len(json.dumps(records_by_table, ensure_ascii=False).encode()) > MAX_DATA_BYTES:
        raise GenerationError("Use at most 100 total records within 128 KiB", 422)
    return records_by_table


def _reference(value):
    if not isinstance(value, dict):
        return None
    if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
        raise GenerationError('Use scalar values or a reference such as {"$ref":"users.0.id"}', 422)
    try:
        table, index, column = value["$ref"].rsplit(".", 2)
        if not table or not index.isascii() or not index.isdecimal() or not column:
            raise ValueError
        return table, int(index), column
    except (ValueError, OverflowError) as error:
        raise GenerationError("References must use table.zero_based_row.column", 422) from error


def _plan(schema, records_by_table):
    tables = {name: selected_table(schema, name) for name in records_by_table}
    nodes = {(name, index): row for name, rows in records_by_table.items() for index, row in enumerate(rows)}
    dependencies = {key: set() for key in nodes}
    for key, row in nodes.items():
        table = tables[key[0]]
        writable = {column["name"] for column in table["columns"] if not column["hidden"]}
        if not row.keys() <= writable:
            raise GenerationError("A record has unknown or generated columns", 422)
        for column, value in row.items():
            if isinstance(value, list):
                raise GenerationError("Encode JSON column values as strings", 422)
            reference = _reference(value)
            if reference:
                name, index, target_column = reference
                if (name, index) not in nodes:
                    raise GenerationError("A reference points to a missing table or record", 422)
                if target_column not in {c["name"] for c in tables[name]["columns"] if not c["hidden"]}:
                    raise GenerationError("A reference points to an unknown column", 422)
                # References follow declared foreign keys, including implicit primary keys.
                foreign_keys = [fk for fk in table["foreign_keys"] if fk["from"] == column and fk["table"].casefold() == name.casefold()]
                primary = sorted((c for c in tables[name]["columns"] if c["pk"]), key=lambda c: c["pk"])
                if not any((fk["to"] or (primary[fk["seq"]]["name"] if fk["seq"] < len(primary) else None)) == target_column for fk in foreign_keys):
                    raise GenerationError("A reference does not match a declared foreign key", 422)
                dependencies[key].add((name, index))
            elif value is not None:
                # Explicit parent IDs supplied in the same batch also insert first.
                for fk in table["foreign_keys"]:
                    if fk["from"] != column:
                        continue
                    for parent_key, parent in nodes.items():
                        if parent_key[0].casefold() != fk["table"].casefold():
                            continue
                        parent_column = fk["to"]
                        if not parent_column:
                            parent_pk = [c for c in tables[parent_key[0]]["columns"] if c["pk"]]
                            parent_column = parent_pk[0]["name"] if len(parent_pk) == 1 else None
                        if parent_column in parent and parent[parent_column] == value and parent_key != key:
                            dependencies[key].add(parent_key)
    order = []
    remaining = dict(dependencies)
    while remaining:
        ready = [key for key, waiting in remaining.items() if not waiting]
        if not ready:
            raise GenerationError("The related records contain cyclic references; remove the cycle before inserting", 422)
        order.extend(ready)
        for key in ready:
            del remaining[key]
        for waiting in remaining.values():
            waiting.difference_update(ready)
    return tables, nodes, order


def _insert_related(database, schema, records_by_table, contract=None):
    tables, nodes, order = _plan(schema, records_by_table)
    inserted = {}
    for key in order:
        row = {}
        for column, value in nodes[key].items():
            reference = _reference(value)
            row[column] = inserted[reference[:2]][reference[2]] if reference else value
        columns = list(row)
        cursor = database.execute(
            f"INSERT OR ABORT INTO {_quote(tables[key[0]]['name'])} ({','.join(_quote(c) for c in columns)}) VALUES ({','.join('?' for _ in columns)}) RETURNING *",
            list(row.values()),
        )
        inserted[key] = dict(zip((column[0] for column in cursor.description), cursor.fetchone()))
        cursor.close()
        if contract is not None:
            check_application_row(contract, key[0], inserted[key], key[1])
    primary_keys = {}
    for name, table in tables.items():
        columns = [c["name"] for c in table["columns"] if c["pk"]]
        primary_keys[name] = [{column: inserted[(name, index)][column] for column in columns} for index in range(len(records_by_table[name]))]
    return primary_keys, [{"table": name, "row": index} for name, index in order]


def _digest(records_by_table, schema, application_revision=None):
    return hashlib.sha256(json.dumps({"records_by_table": records_by_table, "schema": schema, "application_revision": application_revision}, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_related_data(project_id, request):
    records = validate_related_records(request.records_by_table)
    project = get_project(project_id)
    with project_lock(project.directory):
        schema = database_schema(project_id)
        if schema["fingerprint"] != request.expected_schema:
            raise ValueError("Database schema changed; reload definitions before validating")
        _plan(schema, records)
        contract = application_data_contract(project_id, schema)
        require_mockable(contract, records)
        keys, order = {}, []
        validation = {"status": "passed", "message": "All related records passed database and declared application field checks together on a disposable copy"}
        with tempfile.TemporaryDirectory(prefix="reqlica-related-preview-") as folder:
            copied = Path(folder) / "preview.sqlite"
            backup_database(_database_path(project), copied)
            try:
                with closing(_connect(copied)) as database:
                    database.execute("BEGIN IMMEDIATE")
                    keys, order = _insert_related(database, schema, records, contract)
                    database.commit()
            except DataContractError as error:
                validation = {"status": "failed", "message": str(error), "issues": error.issues}
            except sqlite3.Error:
                validation = {"status": "failed", "message": "Related records violate database constraints or use unsupported values. No live records were changed."}
        if database_schema(project_id)["fingerprint"] != request.expected_schema:
            raise ValueError("Database schema changed during preview; reload definitions")
    return {
        "status": "preview", "target": "related", "records_by_table": records,
        "schema_fingerprint": request.expected_schema, "preview_digest": _digest(records, request.expected_schema, contract["revision"]),
        "validation": validation, "insertion_order": order,
        "application_revision": contract["revision"], "application_checks": contract["coverage"],
        "preview_primary_keys": keys if validation["status"] == "passed" else {},
        "counts": {name: len(rows) for name, rows in records.items()},
        "note": "Preview IDs are provisional. References resolve to actual inserted IDs when saving.",
    }


def insert_related_data(project_id, request):
    records = validate_related_records(request.records_by_table)
    project = get_project(project_id)
    with project_lock(project.directory):
        schema = database_schema(project_id)
        contract = application_data_contract(project_id, schema)
        require_mockable(contract, records)
        if _digest(records, request.expected_schema, contract["revision"]) != request.expected_preview_digest:
            raise ValueError("The related records differ from the reviewed preview or the application changed; validate again")
        try:
            with closing(_connect(_database_path(project))) as database:
                database.execute("BEGIN IMMEDIATE")
                schema = database_schema(project_id)
                if schema["fingerprint"] != request.expected_schema:
                    raise ValueError("Database schema changed; nothing was inserted")
                keys, order = _insert_related(database, schema, records, contract)
                database.commit()
        except DataContractError as error:
            raise GenerationError(str(error) + ": " + error.issues[0]["message"] + "; all tables were rolled back", 422) from error
        except sqlite3.Error as error:
            raise GenerationError("The related batch could not be inserted. All tables were rolled back; no automatic retry was made.", 409) from error
    return {
        "status": "inserted", "inserted_count": sum(map(len, records.values())),
        "inserted_counts": {name: len(rows) for name, rows in records.items()},
        "primary_keys": keys, "insertion_order": order,
    }
