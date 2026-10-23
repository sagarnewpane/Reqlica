# Reviewable database setup

Reqlica projects already have a SQLite file. **Database setup** creates the tables
needed by generated APIs without putting schema-changing SQL inside handlers.
This first version is additive: it creates new ordinary tables, primary/foreign
keys, and single/composite unique constraints. It does **not** alter, drop, rename,
or rebuild existing tables, create triggers/views, or run arbitrary SQL.

Existing table changes use the separate [migration workflow](maintenance.md).

## Dashboard workflow

1. Generate database-backed endpoints normally. Groq can include a versioned
   `database/schema.json` file alongside the handlers and tests. Generation saves
   source only; it does not create live tables.
2. Open **Drafts & checks → Database setup**. **Add / edit schema plan** opens the
   existing plan, or prepares an editable starter `users` table in the source
   editor. Nothing is saved until **Save file to draft**.
3. **Preview schema** checks the plan on a disposable copy and displays compiled
   `CREATE TABLE` statements and pending table names. Review the JSON and SQL.
4. Run **Validate** and **Run tests**. Every validation, test run, and draft request
   applies the plan to its own disposable DB copy before loading Python. Tests can
   therefore use new tables before anything is applied to live data. Test writes
   are discarded. Use one test run for signup/login multi-request sequences.
5. **Apply to live database…** requires both checks to pass for the exact saved
   revision, a current draft base, a current preview, and explicit confirmation.
   It locks writers, rechecks the schema fingerprint, saves a private pre-change
   SQLite backup, and creates all missing tables in one transaction. Existing
   tables/records are preserved. No provider call or Python handler is executed.
6. Review the code, approve through the existing CLI, then explicitly activate it.
   Schema application does not approve or publish code. Activation/startup refuse
   a version whose declared tables are missing or incompatible.

Live schema application is persistent even if you never activate the code.
Re-previewing an applied plan reports no pending tables. A repeat apply with a
fresh preview is a no-op; a stale fingerprint/revision/digest is rejected. After
an uncertain HTTP failure, preview again rather than assuming success or failure.

## Plan format

```json
{
  "version": 1,
  "tables": [
    {
      "name": "users",
      "columns": [
        {"name": "id", "type": "INTEGER", "primary_key": true, "nullable": false},
        {"name": "email", "type": "TEXT", "nullable": false, "unique": true},
        {"name": "password_hash", "type": "TEXT", "nullable": false},
        {"name": "created_at", "type": "TEXT", "nullable": false}
      ]
    },
    {
      "name": "sessions",
      "columns": [
        {"name": "id", "type": "INTEGER", "primary_key": true, "nullable": false},
        {
          "name": "user_id", "type": "INTEGER", "nullable": false,
          "references": {"table": "users", "column": "id", "on_delete": "CASCADE"}
        },
        {"name": "token_hash", "type": "TEXT", "nullable": false, "unique": true}
      ]
    }
  ]
}
```

- Names begin with an ASCII letter, then letters/digits/underscores; maximum 63
  characters. `sqlite_*` names and case-insensitive duplicate names are rejected.
- Column types: `INTEGER`, `TEXT`, `REAL`, `BLOB`, `NUMERIC`. At most one primary
  key per table, of type `INTEGER`; SQLite generates its row ID when omitted.
- `nullable` defaults to true; `primary_key` and `unique` default to false.
- `default` accepts finite scalar literals, never SQL expressions. A string such
  as `CURRENT_TIMESTAMP` is literal text, not an evaluated function. Null/omitted
  means no explicit default. Have handlers supply timestamps when appropriate.
- `references` names a table/column, with `on_delete` of `RESTRICT` (default),
  `NO ACTION`, `CASCADE`, or `SET NULL` (requires a nullable column). Referenced
  columns must exist and satisfy SQLite foreign-key uniqueness requirements.
- Optional table `unique_together` contains arrays of column names, for example
  `[["user_id", "token_hash"]]`.
- Maximum 64 KiB, 20 tables, 50 columns/table, 20 composite unique constraints/table,
  16,384 characters per compiled table definition, and 1 KiB/string default.
  Applying must remain within the 200-object metadata inspection limit.
  Extra fields and duplicate JSON keys are rejected.

Plans are cumulative versioned declarations: preserve old entries and append new
tables. Existing declared tables must match the compiled `CREATE TABLE` SQL
exactly. A pre-existing manually created table with equivalent but differently
formatted SQL may be rejected deliberately: omit it from the plan and use its
actual live definitions instead. This prevents silently accepting weaker or
different constraints. Never remove/re-add a declaration to simulate an alteration.

## Console API

Paths start with `/api/projects/{project_id}/drafts/{draft_id}/database`:

- `POST /preview`, body `{ "expected_revision": "<current SHA-256>" }`.
  Returns the plan, SQL, pending tables, `schema_fingerprint`, `preview_digest`,
  revision, and `live_changes: false`. An absent plan reports no changes.
- `POST /apply`, body containing `expected_revision`, `expected_schema` from the
  preview fingerprint, `expected_preview_digest` from the preview, and the JSON
  boolean **`confirm: true`**. Returns `applied` / `already_applied`, created table
  names, revision, and a project-relative backup path when changes were made.

Use normal draft file writes to save a plan. Any file edit invalidates checks,
approval, and the browser's preview. No new approval or activation endpoint exists.
Project context exposes proposed `database_setup` separately from live `database`
definitions; it never reports the proposal as already applied or includes rows.

Backups are stored under `.reqlica/database-backups/` with mode `0600` and include
existing records. They are flushed before schema application. Retain them safely;
there is no automatic retention pruning or one-click restore. Stop workers before
any deliberate manual restoration. A failed schema transaction rolls back; a
failed backup prevents schema changes. SQLite transactions and metadata checks
protect against concurrent writers/schema drift, not untrusted Python or hostile
filesystem changes. Management APIs remain unauthenticated and trusted-local.

## Authentication boundary

A users table enables persistent signup and credential verification; it does not
implement them. Handlers must hash/verify passwords correctly. JWT signing keys,
secret configuration, token issuance/verification, TLS, and abuse protection are
separate concerns. Use [Project environment](environment.md) to supply/generate
JWT signing keys; do not put secrets in schema defaults. Database setup itself
does not provision keys, install dependencies, or provide production-ready auth.
