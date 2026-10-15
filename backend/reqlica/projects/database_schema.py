"""Read SQLite definitions, never application records or generated Python."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing

from reqlica.projects.drafts import _contained

MAX_OBJECTS = 200
MAX_ITEMS = 500
MAX_SQL = 16_384


def read_database_schema(project, tables: list[str] | None = None) -> dict:
    result = {
        "engine": "sqlite",
        "path": str(project.directory / "data.sqlite"),
        "scope": "current_live_database_schema",
        "records_included": False,
        "draft_note": "Draft checks copy this live database; this is not a prediction of draft migrations.",
        "tables": [],
        "views": [],
        "triggers": [],
        "relationships": [],
        "truncated": False,
        "limits": {
            "objects": MAX_OBJECTS,
            "items_per_object": MAX_ITEMS,
            "sql_characters": MAX_SQL,
        },
    }
    try:
        if tables is not None and (
            len(tables) > 50 or any(not isinstance(name, str) for name in tables)
        ):
            raise ValueError("Select at most 50 database table names")
        path = _contained(project.directory, "data.sqlite")
        for suffix in ("-wal", "-shm"):
            _contained(project.directory, "data.sqlite" + suffix)
        if not path.exists():
            return {
                **result,
                "status": "missing",
                "error": "Project database does not exist; it was not created by this read",
            }
        deadline = time.monotonic() + 2.0
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
        ) as database:
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA query_only = ON")
            database.execute("PRAGMA trusted_schema = OFF")
            database.set_progress_handler(
                lambda: int(time.monotonic() > deadline), 1000
            )
            database.execute("BEGIN")
            result["schema_version"] = database.execute(
                "PRAGMA schema_version"
            ).fetchone()[0]
            result["user_version"] = database.execute("PRAGMA user_version").fetchone()[
                0
            ]
            where = "name NOT GLOB 'sqlite_*' AND type IN ('table', 'view', 'trigger')"
            parameters = []
            if tables is not None:
                placeholders = ",".join("?" for _ in tables) or "NULL"
                available = {
                    row[0]
                    for row in database.execute(
                        f"SELECT name FROM sqlite_schema WHERE type='table' AND name IN ({placeholders})",
                        tables,
                    )
                }
                unknown = set(tables) - available
                if unknown:
                    raise ValueError(
                        f"Unknown database tables: {', '.join(sorted(unknown))}"
                    )
                where += f" AND (name IN ({placeholders}) OR (type='trigger' AND tbl_name IN ({placeholders})))"
                parameters = [*tables, *tables]
            result["object_count"] = database.execute(
                f"SELECT COUNT(*) FROM sqlite_schema WHERE {where}", parameters
            ).fetchone()[0]
            result["truncated"] = result["object_count"] > MAX_OBJECTS
            objects = database.execute(
                f"SELECT type, name, tbl_name, substr(sql, 1, ?) AS sql, length(sql) > ? AS sql_truncated "
                f"FROM sqlite_schema WHERE {where} ORDER BY name LIMIT ?",
                [MAX_SQL, MAX_SQL, *parameters, MAX_OBJECTS],
            ).fetchall()

            def rows(sql: str, name: str) -> list[dict]:
                if time.monotonic() > deadline:
                    raise TimeoutError("Database schema inspection timed out")
                values = database.execute(sql, (name,)).fetchmany(MAX_ITEMS + 1)
                if len(values) > MAX_ITEMS:
                    result["truncated"] = True
                return [dict(row) for row in values[:MAX_ITEMS]]

            for item in objects[:MAX_OBJECTS]:
                if time.monotonic() > deadline:
                    raise TimeoutError("Database schema inspection timed out")
                definition = dict(item)
                sql = definition.get("sql") or ""
                definition["sql"] = sql[:MAX_SQL]
                definition["sql_truncated"] = bool(item["sql_truncated"])
                result["truncated"] |= definition["sql_truncated"]
                if item["type"] != "table":
                    result["views" if item["type"] == "view" else "triggers"].append(
                        definition
                    )
                    continue
                definition["columns"] = rows(
                    "SELECT * FROM pragma_table_xinfo(?)", item["name"]
                )
                definition["foreign_keys"] = rows(
                    "SELECT * FROM pragma_foreign_key_list(?)", item["name"]
                )
                definition["indexes"] = rows(
                    "SELECT * FROM pragma_index_list(?)", item["name"]
                )
                for index in definition["indexes"]:
                    index["columns"] = rows(
                        "SELECT * FROM pragma_index_xinfo(?)", index["name"]
                    )
                    statement = database.execute(
                        "SELECT substr(sql, 1, ?), length(sql) > ? FROM sqlite_schema WHERE type='index' AND name=?",
                        (MAX_SQL, MAX_SQL, index["name"]),
                    ).fetchone()
                    index_sql = (statement[0] if statement else None) or ""
                    index["sql"] = index_sql[:MAX_SQL]
                    index["sql_truncated"] = bool(statement and statement[1])
                    result["truncated"] |= index["sql_truncated"]
                grouped = {}
                for foreign_key in definition["foreign_keys"]:
                    relationship = grouped.setdefault(
                        foreign_key["id"],
                        {
                            "from_table": item["name"],
                            "to_table": foreign_key["table"],
                            "from_columns": [],
                            "to_columns": [],
                            "on_update": foreign_key["on_update"],
                            "on_delete": foreign_key["on_delete"],
                        },
                    )
                    relationship["from_columns"].append(foreign_key["from"])
                    relationship["to_columns"].append(foreign_key["to"])
                result["relationships"].extend(grouped.values())
                result["tables"].append(definition)
            result["fingerprint"] = hashlib.sha256(
                json.dumps(result, sort_keys=True).encode()
            ).hexdigest()
        return {**result, "status": "available", "error": None}
    except (OSError, sqlite3.Error, ValueError) as error:
        return {**result, "status": "error", "error": str(error)}
