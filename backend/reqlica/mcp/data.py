"""Data-only previews for AI clients. Live insertion remains a human action."""

from typing import Annotated, Literal

from pydantic import Field
from starlette.concurrency import run_in_threadpool

from reqlica.generation.data_schemas import DataValidationRequest, RelatedDataValidationRequest
from reqlica.generation.json_output import parse_json
from reqlica.mcp.parameters import ProjectID
from reqlica.projects.data_contracts import application_data_contract
from reqlica.projects.mock_data import database_schema, validate_data
from reqlica.projects.related_data import validate_related_data


async def get_mock_data_context(project_id: ProjectID) -> dict:
    """Read writable live table definitions, relationships and application field contracts.

    No records, secret values or handler execution. Use the returned schema
    fingerprint with preview_mock_data. Respect unsupported_tables: do not invent
    password hashes, encrypted values, custom validators or guessed existing IDs.
    Proposed draft schemas are not evidence that live tables already exist.
    """
    def read():
        schema = database_schema(project_id)
        return {"schema": schema, "application": application_data_contract(project_id, schema)}

    return await run_in_threadpool(read)


async def preview_mock_data(
    project_id: ProjectID,
    records_json: Annotated[str, Field(min_length=1, max_length=131072, description="JSON-encoded record array for json/sqlite, or a table-to-record-arrays object for related. At most 100 records and 128 KiB; related foreign keys may use $ref objects.")],
    target: Annotated[Literal["json", "sqlite", "related"], Field(description="Preview destination: json is a downloadable fixture; sqlite validates one existing table; related validates linked rows across existing tables. No live insertion.")] = "json",
    table: Annotated[str | None, Field(min_length=1, max_length=128, description="Exact existing table name for sqlite only. Omit for json and related.")] = None,
    expected_schema: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$", description="Current database fingerprint returned by get_mock_data_context.schema.fingerprint. Required for sqlite/related; omit for json.")] = None,
) -> dict:
    """Prepare and validate synthetic records for human review. NEVER inserts live rows.

    records_json is a JSON-encoded array for json/sqlite, or an object mapping
    existing table names to arrays for related. At most 100 records / 128 KiB.
    For SQLite/related read get_mock_data_context first and supply its fingerprint.
    Related foreign keys use references such as {"$ref":"users.0.id"}; the manual
    importer resolves real IDs later. Validation uses disposable database copies
    and the same application-format/refusal checks as the manual data screen.
    Inspect validation.status; failed previews are feedback, not ready to insert.
    The dashboard offers review/edit/download and separately confirmed insertion.
    """
    def preview():
        records = parse_json(records_json)
        if target == "related":
            if table is not None:
                raise ValueError("Related previews select tables through records_json, not table")
            return validate_related_data(project_id, RelatedDataValidationRequest(
                records_by_table=records, expected_schema=expected_schema,
            ))
        return validate_data(project_id, DataValidationRequest(
            target=target, table=table, expected_schema=expected_schema, records=records,
        ))

    return await run_in_threadpool(preview)
