# Custom mock data

Open **Mock data** in the standalone dashboard. Choose **JSON fixture** for any
custom record shape, or **Existing SQLite table** to prepare persistent records
for database-backed endpoints. This does not automatically wire JSON fixtures into
an endpoint or create database tables. To create tables first, use the separate
[Database setup](database-setup.md) flow in **Drafts & checks**, then reload tables
in Mock data.

## Workflow

1. Describe the records, choose 1–100 records, and provide an example object.
   For JSON fixtures it defines the field shape/types; values are illustrative.
   Nested objects/arrays are supported. For SQLite it is optional field guidance,
   useful for supplying existing foreign-key IDs; actual columns come from the DB.
2. **Generate records** makes one confirmed Groq request. Only the description,
   template, project name/ID, and selected table definitions are sent—not existing
   records, published code, chat history, or provider credentials in model input.
   The model returns data, never executable SQL or Python.
3. Review and edit **JSON records**. **Use template** instead prepares local copies
   without an AI call or adding invented fields. **Copy JSON** / **Download** export
   the reviewed array without writing project files or changing live data.
4. For SQLite, load/select a table and **Validate records** after manual changes.
   This checks the batch on a disposable copy of the current DB, including existing
   unique values and foreign keys. It commits only that temporary copy to check
   deferred foreign keys, then deletes the copy. Existing rows are not sent to AI.
   Failed constraints leave the generated records editable, with a visible error.
5. **Insert records…** is enabled only for the current passed preview. Confirm the
   destination table and record count. Insertion appends atomically to persistent
   project data; it never approves/activates code or calls a generated handler.
   A schema change, record edit, or constraint conflict requires revalidation.

Related IDs must come from your requirements/template or be edited manually. The
generator cannot know which existing IDs are valid because it receives no rows.
Use existing endpoints to inspect records when needed; this release has no generic
record browser, schema creation/migration, overwrite, reset, or deletion action.

## Console API

All paths below start with `/api/projects/{project_id}/data`.

| Method/path | Purpose |
| --- | --- |
| `GET /schema` | Current SQLite definitions, columns/relationships and fingerprint; no rows or filesystem path. |
| `POST /generate` | One Groq request, then a JSON/SQLite preview; no live write. |
| `POST /validate` | Check edited records locally; SQLite uses a disposable DB copy. |
| `POST /insert` | Explicitly confirmed, atomic append to an existing table. |

Generate a fixture:

```json
{
  "target": "json",
  "collection": "books",
  "requirements": "Three varied fictional books; include one out of stock.",
  "count": 3,
  "template": {"title": "Example", "price": 14.99, "in_stock": true},
  "model": "openai/gpt-oss-20b",
  "temperature": 0.2,
  "max_tokens": 8192
}
```

For SQLite, use `target: "sqlite"`, an existing `table`, and `expected_schema`
copied from `/schema`'s `fingerprint`. Do not supply `table`/`expected_schema` for
JSON fixtures. A Groq key can be supplied only through `X-Reqlica-Groq-Key`, or the
console uses `GROQ_API_KEY` / `GROQ_API`. Fixed provider origin/no retries apply.

Generate/validate return `status: "preview"`, `target`, `table`, `records`,
`schema_fingerprint`, `preview_digest`, and `validation` (`status`/`message`).
Generation also returns a summary, assumptions, and provider usage. A preview with
`validation.status: "failed"` is not insertable, even if the HTTP request succeeded.

To validate, send `{target, table, expected_schema, records}` (omit table/schema
for JSON). To insert, send the same SQLite fields plus
`expected_preview_digest` from the exact passed preview and **`confirm: true`**.
The response reports `status: "inserted"`, `inserted_count`, and new `row_ids`
(null for WITHOUT ROWID tables). Both requests validate data independently;
insertion rechecks constraints and schema inside its write transaction.

## Boundaries and recovery

- Maximum 100 records, 128 KiB per batch, 50 fields per object, six nesting levels,
  100 items per nested array, and 8 KiB per individual string. Numbers must be
  finite; integers must fit signed 64 bits. Templates fit within 16 KiB;
  requirements within 16,384 characters. Model output stays within 256 KiB and
  the complete provider prompt within 96 KiB. No silent truncation or repair.
- JSON generation validates exact template keys/types (null permits an unspecified
  type; a sample array defines its item shape). SQLite permits writable column
  names only and scalar values; JSON columns must be explicitly encoded as text.
- Only ordinary, existing tables are supported. Virtual/shadow/system tables,
  views, generated columns, and target tables with triggers cannot be seeded by
  this importer. SQL identifiers are checked/quoted; values use bound parameters.
- `INSERT OR ABORT` overrides schema-level `REPLACE`/`IGNORE` conflict policies.
  If any row fails, the entire batch rolls back; existing records are not replaced.
  Normal defaults and generated columns can still be computed by SQLite.
- Validation is a snapshot, not a reservation of IDs/unique values. Concurrent
  writes may make a previously valid batch fail; no automatic retry occurs.
  Inserts are **not idempotent**: do not resend after a client timeout until you
  check the live endpoint/data. The UI disables the insert after success or an
  uncertain failure and requires revalidation before another attempt.
- Preview data is held only in page memory and is lost on refresh unless exported.
  Nothing is stored with provider profiles/chat history. Successful SQLite inserts
  persist independently of draft activation. The APIs remain trusted-local and
  unauthenticated; keep the console/gateway on loopback.
