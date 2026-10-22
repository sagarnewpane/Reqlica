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
2. **Prepare with Project AI** prefills the shared conversation with these inputs;
   it makes no model request. Review the provider/context permissions and send the
   message. The AI can read live definitions and published application contracts,
   then validate synthetic JSON, SQLite, or linked-table previews through MCP.
   Existing database rows and environment values are excluded from context; trusted
   code execution, if separately permitted, can expose values in its output.
   The AI cannot insert live data. Use **Review mock data** to return the artifact
   to this screen. Earlier data-generation APIs below remain available.
3. Review and edit **JSON records**. **Use template** instead prepares local copies
   without an AI call or adding invented fields. **Copy JSON** / **Download** export
   the reviewed array without writing project files or changing live data.
4. For SQLite, load/select a table and **Validate records** after manual changes.
   This checks the batch on a disposable copy of the current DB, including existing
   unique values and foreign keys. It commits only that temporary copy to check
   deferred foreign keys, then deletes the copy. Existing rows are not sent to AI.
   Edited previews with failed constraints remain editable. Invalid generated
   batches are refused rather than presented as usable data.
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

## Related records from one prompt

Choose **Related database records** in Mock data, then describe the whole dataset:
“Create 2 users and 2 posts, one post for each user.” The provider receives the
existing table definitions and relationships. It chooses the tables and counts
from your description; no request sequence or separate per-table prompt is needed.

The preview groups editable records by table and shows the per-table counts.
Parent rows are inserted before their children. Foreign keys use references such
as `{"$ref":"users.0.id"}` (zero-based row index), which resolve to actual generated
IDs at insertion time. IDs shown in the disposable preview are provisional;
concurrent inserts do not break these references.

Validate checks the full dataset on a disposable SQLite copy. **Insert records**
requires confirmation and saves all tables in one transaction. If any row fails,
the entire batch rolls back. Existing rows are retained. No automatic retry is
made. Generation itself never writes live records.

Limits: 10 existing ordinary tables, 100 total records, 128 KiB. Tables with
triggers and virtual tables remain unsupported. References must follow declared
foreign keys. Cyclic references are rejected; schema creation remains a separate
Database setup action. Application-specific hashing and custom validation are refused rather than approximated.

Endpoints: `POST /api/projects/{id}/data/related/generate`, `/related/validate`,
and `/related/insert`. Validation/import use `records_by_table` and
`expected_schema`; insertion also requires the reviewed `expected_preview_digest`
and `confirm: true`.

HTTP request sequences remain available separately in Request tester. They execute
real API calls and can reference earlier responses; they are not used to generate
related database records.

Selecting a SQLite table fills the example field from its writable columns,
omitting automatic integer primary keys. Edit placeholders and foreign-key IDs
before generating or inserting data; the example does not read existing rows.

Settings → Shared request headers saves a bearer token and arbitrary allowed
headers per project in browser local storage. Request-specific headers override
defaults case-insensitively. These defaults apply to live API calls, disposable draft calls, and
chains, not provider or management calls. Clear the fields
and save to remove defaults. Browser storage is unencrypted.

Provider API keys now persist with browser profiles, including across reloads.
Clear a provider key and save, or remove the profile, to delete it. Project
runtime environment values persist separately in `.reqlica/environment.json`;
values remain write-only and are loaded on worker start/restart.


## Application compatibility and refusal

SQLite types alone are insufficient: a TEXT column may hold a UUID expected by
an application model. Database mock generation now reads published Python and
cached API schemas without importing project code or reading existing rows.
Simple declared types, UUID/date formats, enum values, and field constraints are
checked on the disposable copy and again during insertion. Related foreign keys
inherit recognized ID formats. Preview digests also pin the application source
revision; code changes require a fresh validation.

This is intentionally limited. Tables with recognized password/hash, credential,
encryption or binary fields, custom Python validators/serializers, nested storage
or unsupported field schemas are refused with **“This data can't be mocked
reliably”** and a reason. Use the application's own create/signup endpoints for
those records. The provider may also explicitly refuse an uncertain request.
Invalid generated records are refused without a data repair retry, and the UI
clears the failed preview rather than offering it for insertion. No live rows
are written during generation. Existing invalid records require separate cleanup.

The provider receives published model/handler/helper source and inferred field
rules in addition to database definitions. Runtime environment files and values,
provider keys, test sources, and existing database rows are excluded from this
context. Application code must not contain embedded production credentials.
