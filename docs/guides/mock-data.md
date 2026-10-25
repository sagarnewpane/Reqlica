# Mock data

Open **Sample data** in the dashboard to prepare JSON fixtures or append reviewed
records to existing SQLite tables. This screen does not create tables or wire a
JSON fixture into an endpoint. Use [Database setup](database-setup.md) or
[migrations](maintenance.md) for schema changes.

## Prepare and review

1. Choose **JSON fixture**, **Existing SQLite table**, or **Related database records**.
   Load current definitions before selecting SQLite tables.
2. Enter records manually, use **Insert record template**, load a saved dataset,
   or choose **Generate in chat** and describe the complete dataset. Specify counts,
   target tables, formats, and relationships. Opening chat does not call the model.
3. Project AI uses `get_mock_data_context` for SQLite definitions/application
   contracts, then `preview_mock_data` for validation. Existing rows and private
   environment values are excluded from this context; separately permitted Python
   execution can still expose values in its output.
4. A passed AI preview ends the turn immediately and skips queued calls. Inspect
   the displayed records, then choose **Review mock data** to transfer them to
   Sample data. The AI waits for your explicit reply before generating more.
   Data-only work requires no code draft.
5. Edit the JSON and choose **Validate records**. The chat handoff and saved
   datasets require fresh validation before insertion. **Copy JSON**, **Download
   JSON**, and named dataset saving do not insert live records.
6. For SQLite/related data, **Insert reviewed records…** is enabled only for the
   current passed preview. Confirm destinations and counts. Insertion rechecks
   schema, source contracts, constraints, and the exact preview digest.

Validation uses a disposable SQLite backup, including existing unique values and
foreign keys. Deferred constraints are checked by committing only that copy,
which is then removed. Failed previews cannot be inserted. The agent can correct
rejected data within its bounded recovery loop; missing IDs or unsupported formats
need user input or the application's own create endpoints.

## Related records

Request the whole dataset in one prompt, for example “Create two categories and
three products, each linked to one new category.” All selected tables must exist.
Use declared foreign-key references to new rows rather than guessing generated IDs:

```json
{
  "categories": [{ "name": "Books" }],
  "products": [
    { "category_id": { "$ref": "categories.0.id" }, "name": "Notebook" }
  ]
}
```

The row index is zero-based. The importer inserts parents first and resolves
actual IDs during insertion; preview IDs are provisional. References must follow
declared foreign keys. Cycles are rejected. Existing IDs must be supplied or
verified separately because model context contains no live records.

All tables are inserted in one transaction. Any failure rolls back the full
batch and preserves existing records. Request chains are a different workflow:
they execute real API requests and can refer to earlier responses.

## Console API and MCP

Paths below start with `/api/projects/{project_id}/data`.

| Method/path              | Purpose                                                                       |
| ------------------------ | ----------------------------------------------------------------------------- |
| `GET /schema`            | SQLite definitions, relationships, and fingerprint; no rows or local DB path. |
| `POST /validate`         | Validate a JSON or single-table SQLite batch.                                 |
| `POST /insert`           | Explicitly confirmed atomic single-table append.                              |
| `POST /related/validate` | Validate a linked multi-table batch.                                          |
| `POST /related/insert`   | Explicitly confirmed atomic multi-table append.                               |

Single-table validation accepts `{target, table, expected_schema, records}`;
omit table/schema for JSON fixtures. Related validation accepts
`{records_by_table, expected_schema}`. Responses include `status: "preview"`,
records, fingerprints/digests, and `validation`. Failed validation is feedback,
not a usable insertion approval.

Insertion adds the reviewed `expected_preview_digest` and literal `confirm: true`
to the corresponding request. Schema/source/record changes require revalidation.
Use MCP's `get_mock_data_context` and `preview_mock_data` for AI preparation;
there is no AI live-insert tool or separate data-generation REST API.

## Bounds and supported formats

- At most 100 total records, 128 KiB per batch, and 10 tables for related records.
  Objects have at most 50 fields, six nesting levels, 100 items per nested array,
  and 8 KiB per string. Numbers must be finite and integers fit signed 64 bits;
  the browser rejects integers it cannot represent safely.
- JSON fixtures are bounded record objects. Example/template values are guidance,
  not an enforced output schema. SQLite values are scalar; encode JSON columns
  explicitly as text. Automatic/default/generated values are left to SQLite;
  generated columns cannot be supplied as writable fields.
- Virtual/system/shadow tables, views, and tables with triggers are unsupported
  insertion targets. Identifiers are checked/quoted and values use bound SQL
  parameters. `INSERT OR ABORT` prevents replacing existing rows under schema
  conflict policies.
- Published Python and cached API schemas supply supported field contracts
  without importing application code. Recognized UUID/date formats, enum values,
  declared types, and constraints are checked on preview and insertion. Related
  references inherit recognized ID formats.
- Recognized password/hash, encrypted, credential, binary, custom validator,
  nested-storage, and unsupported schemas are refused with a reason. Use the
  application's signup/create endpoints rather than fabricating credential data.
  Passing these checks does not prove all application business rules.

## Persistence and recovery

Validation is a snapshot, not a reservation of IDs or unique values. Concurrent
writes can invalidate a passed preview; insertion rechecks current state. Inserts
are **not idempotent**. After a timeout or unconfirmed response, inspect live data
before trying again. The UI requires acknowledgement and fresh validation rather
than automatically resending a possibly completed insert.

Unsaved editor data lives in page memory. Chat handoff temporarily uses session
storage; AI preview results also remain in saved conversation checkpoints. Save a
named dataset or download JSON before leaving/refreshing. Provider profiles do
not store datasets. Successful SQLite inserts persist independently of code
activation. There is no generic live-record editor, overwrite, or undo here;
use the project's endpoints, [baselines](reset.md), or reviewed maintenance tools.

Shared request-header values live in SPA memory and clear on refresh. Provider
keys are stored separately on the backend; project environment values remain
private in `.reqlica/environment.json`. See [Project AI](generation.md) and
[environment](environment.md) for credential boundaries.

[Documentation index](../README.md) · [Project overview](../../README.md)
