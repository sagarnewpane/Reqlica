# Mock data

Use **Sample data** to prepare JSON fixtures or append records to existing SQLite
tables. Create tables separately through [database setup](database-setup.md).

## Prepare and insert

1. Choose JSON, an existing SQLite table, or related database records.
2. Enter JSON, load a dataset, or choose **Generate in chat** with counts and relationships.
3. Review the preview. Passed AI previews stop the turn; **Review mock data**
   transfers records to Sample data.
4. Edit and choose **Validate records**. Handoffs and saved datasets need fresh validation.
5. For SQLite, choose **Insert reviewed records…** and confirm tables/counts.

Validation runs on a disposable database copy. Insertion rechecks schema,
contracts, constraints, and the preview digest before an atomic append.
A failed batch rolls back without replacing existing records.

## Related records

New rows can reference each other without guessing generated IDs:

```json
{
  "categories": [{ "name": "Books" }],
  "products": [
    { "category_id": { "$ref": "categories.0.id" }, "name": "Notebook" }
  ]
}
```

References use `table.zero_based_row.column` and declared foreign keys. Parents
are inserted first; cycles are rejected. Existing IDs must be supplied or verified
separately because ordinary model context excludes existing rows.

## Limits

- 100 records / 128 KiB per batch; at most 10 related tables.
- SQLite values must be scalar. JSON storage fields need explicit text encoding.
- Views, virtual/system tables, trigger-backed insertion, binary/credential formats,
  password hashes, and unsupported validators are refused. Use application endpoints.
- Preview IDs and unique values are not reserved. Concurrent writes can invalidate them.
- Inserts are not idempotent. Inspect live data before retrying after a lost response.

Save a named dataset or download JSON before leaving the page. Unsaved editor data
is temporary; chat handoff uses session storage and previews remain in saved chat.
There is no generic live-record editor or undo. Use endpoints or [reset](reset.md).

## API and MCP

Console prefix: `/api/projects/{project_id}/data`.
`GET /schema` reads definitions. `POST /validate` and `/related/validate` check
batches; `/insert` and `/related/insert` require the reviewed digest and `confirm: true`.
Single-table requests use `{target, table, expected_schema, records}`; related
requests use `{records_by_table, expected_schema}`. Omit table/schema for JSON.

MCP exposes `get_mock_data_context` and `preview_mock_data`; neither inserts rows.

[Documentation](../README.md)
