"""Bounded, declarative, additive SQLite setup. Never execute supplied SQL."""

import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from reqlica.projects.database_schema import MAX_SQL
from reqlica.projects.drafts import _contained

SCHEMA_PLAN_PATH = "database/schema.json"
MAX_PLAN_BYTES = 64 * 1024


def identifier(value: str) -> str:
    if not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,62}", value
    ) or value.lower().startswith("sqlite_"):
        raise ValueError(
            "Schema names must be simple identifiers, at most 63 characters, not sqlite_* names"
        )
    return value


def quote(value: str) -> str:
    return '"' + identifier(value) + '"'


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    table: str
    column: str
    on_delete: Literal["RESTRICT", "NO ACTION", "CASCADE", "SET NULL"] = "RESTRICT"


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    type: Literal["INTEGER", "TEXT", "REAL", "BLOB", "NUMERIC"]
    primary_key: bool = False
    nullable: bool = True
    unique: bool = False
    default: str | int | float | bool | None = None
    references: Reference | None = None


class Table(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    columns: list[Column] = Field(min_length=1, max_length=50)
    unique_together: list[list[str]] = Field(default_factory=list, max_length=20)


class SchemaPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    tables: list[Table] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid_names(self):
        names = set()
        for table in self.tables:
            name = identifier(table.name).lower()
            if name in names:
                raise ValueError("Duplicate table name")
            names.add(name)
            columns = set()
            for column in table.columns:
                name = identifier(column.name).lower()
                if name in columns:
                    raise ValueError("Duplicate column name")
                columns.add(name)
                if column.primary_key and column.type != "INTEGER":
                    raise ValueError(
                        "This setup version supports only an INTEGER primary key"
                    )
                if column.default is not None:
                    literal(column.default)
                if column.references:
                    identifier(column.references.table)
                    identifier(column.references.column)
                    if (
                        column.references.on_delete == "SET NULL"
                        and not column.nullable
                    ):
                        raise ValueError("SET NULL requires a nullable column")
            if sum(c.primary_key for c in table.columns) > 1:
                raise ValueError("Use at most one primary key per table")
            for unique in table.unique_together:
                if (
                    not unique
                    or len(unique) != len({c.lower() for c in unique})
                    or not {c.lower() for c in unique} <= columns
                ):
                    raise ValueError(
                        "Unique constraints must reference distinct columns in their table"
                    )
        return self


def literal(value) -> str:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 1024 or "\x00" in value:
            raise ValueError("Default strings must fit in 1 KiB and contain no NUL")
        return "'" + value.replace("'", "''") + "'"
    if type(value) is bool:
        return "1" if value else "0"
    if type(value) is int and -(2**63) <= value < 2**63:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return repr(value)
    raise ValueError("Defaults must be bounded scalar values, never SQL expressions")


def parse_plan(text: str) -> SchemaPlan:
    if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
        raise ValueError("Database schema plan exceeds 64 KiB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate schema plan keys")
            result[key] = value
        return result

    try:
        return SchemaPlan.model_validate(json.loads(text, object_pairs_hook=unique))
    except RecursionError as error:
        raise ValueError("Database schema plan is too deeply nested") from error


def load_plan(directory: Path) -> SchemaPlan | None:
    path = _contained(directory, SCHEMA_PLAN_PATH)
    if not path.exists():
        return None
    if path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError("Database schema plan exceeds 64 KiB")
    return parse_plan(path.read_text(encoding="utf-8"))


def statements(plan: SchemaPlan) -> list[tuple[str, str]]:
    result = []
    for table in plan.tables:
        definitions = []
        for column in table.columns:
            sql = quote(column.name) + " " + column.type
            if column.primary_key:
                sql += " PRIMARY KEY"
            if not column.nullable:
                sql += " NOT NULL"
            if column.unique:
                sql += " UNIQUE"
            if column.default is not None:
                sql += " DEFAULT " + literal(column.default)
            if column.references:
                ref = column.references
                sql += f" REFERENCES {quote(ref.table)} ({quote(ref.column)}) ON DELETE {ref.on_delete}"
            definitions.append(sql)
        for unique in table.unique_together:
            definitions.append("UNIQUE (" + ", ".join(quote(c) for c in unique) + ")")
        sql = f"CREATE TABLE {quote(table.name)} (" + ", ".join(definitions) + ")"
        if len(sql) > MAX_SQL:
            raise ValueError(
                "Each compiled table definition must fit within 16,384 characters"
            )
        result.append((table.name, sql))
    return result


def setup(
    database: sqlite3.Connection, plan: SchemaPlan | None, *, create: bool
) -> list[str]:
    """Apply to a caller-owned transaction, or detect unapplied tables read-only.

    Existing declarations must match our compiled SQL exactly. Conservative by
    design: do not silently accept weaker constraints on a pre-existing table.
    """
    if plan is None:
        return []
    pending = []
    for name, sql in statements(plan):
        existing = database.execute(
            "SELECT type, name, sql FROM sqlite_schema WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if existing:
            if existing[0] != "table" or existing[1] != name or existing[2] != sql:
                raise ValueError(
                    f"Existing object {name} conflicts with the schema plan; existing tables are never altered"
                )
        else:
            pending.append(name)
            if create:
                database.execute(sql)
    if create or not pending:
        # Preparing (not executing) writes detects missing/non-unique FK targets.
        for table in plan.tables:
            for column in table.columns:
                if column.references:
                    ref = column.references
                    cols = list(
                        database.execute(
                            "SELECT name FROM pragma_table_info(?)", (ref.table,)
                        )
                    )
                    if ref.column not in {c[0] for c in cols}:
                        raise ValueError(
                            f"Missing referenced column {ref.table}.{ref.column}"
                        )
            database.execute(
                f"EXPLAIN INSERT INTO {quote(table.name)} DEFAULT VALUES"
            ).fetchall()
    return pending


def prepare_database(directory: Path, path: Path) -> list[str]:
    from reqlica.projects.migrations import prepare_migration
    prepare_migration(directory, path)
    plan = load_plan(directory)
    if plan is None:
        return []
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    ) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("BEGIN IMMEDIATE")
        try:
            pending = setup(db, plan, create=True)
            db.commit()
            return pending
        except BaseException:
            db.rollback()
            raise


def require_database_ready(directory: Path, path: Path) -> None:
    from reqlica.projects.migrations import load_migration
    from reqlica.projects.reset import _fingerprint
    migration = load_migration(directory)
    if migration is not None and _fingerprint(path) != migration.target_schema:
        raise ValueError("Apply the reviewed migration before starting this API")
    plan = load_plan(directory)
    if plan is None:
        return
    try:
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        ) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA trusted_schema = OFF")
            if setup(db, plan, create=False):
                raise ValueError(
                    "Apply the reviewed draft database setup before activating or starting this API"
                )
    except sqlite3.Error as error:
        raise ValueError(
            "Declared database relationships are not ready; review the schema plan and live definitions"
        ) from error
