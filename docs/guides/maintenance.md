# Backend maintenance

All operations here are local management APIs, not AI approval or publication
permissions. Except preview/read endpoints, requests require JSON `confirm: true`.
Worker-owned operations fence new traffic, drain requests (five-second timeout),
stop the project worker and retain its stopped state. Other projects remain usable.
Explicitly start or activate compatible code afterward. Stop state follows the
existing gateway lifecycle; it does not survive a gateway restart.

## Relocate a project

POST `/api/projects/{project_id}/relocate` through console or gateway:

```json
{
  "expected_revision": "<active code revision from project context>",
  "expected_directory": "/old/absolute/project",
  "directory": "/new/absolute/project",
  "confirm": true
}
```

The destination must not exist; its parent must exist on the same filesystem.
The move preserves project ID, versions, drafts, data, settings, histories and
private variables. Source and destination cannot overlap. The registry is updated
atomically. A registry-side relocation journal lets a later project open recover
a crash between the directory rename and registry update. Cross-filesystem moves
use archive export/import instead. Relocation does not rewrite absolute paths
hardcoded in application code or records.

## Full workspace archives

POST `/api/projects/{project_id}/workspace-export` with `expected_revision`
(active code hash), `confirm: true`, and optional `include_secrets: true`.
It returns a ZIP download after stopping/draining the worker. The default excludes
private environment values. The explicit secret option includes them.

Workspace archives preserve all version/draft source, saved checks/approvals,
OpenAPI artifacts, conversations, scenario/auth/CORS settings, inspection history,
baselines and recovery snapshots. They are sensitive backups even without the
private environment file. Runtime lock files and SQLite journals are excluded;
live SQLite databases are copied using SQLite backup. Immutable backup files are
copied byte-for-byte so their stored checksums remain valid. Unpublished draft
Python is archived without running it.

POST raw ZIP bytes to the console at
`/api/projects/workspace-import?directory=/new/absolute/path`. Imports preserve the
original ID and require it to be unregistered. Cloning full workspaces is not
supported: use the active-project clone export when you need a new identity.
Files are validated/staged before registration, without code execution. The
limits match active bundles: 32 MiB ZIP, 128 MiB unpacked, 2,000 files, 1 MiB
metadata. Existing directories/registrations are never overwritten. Legacy
projects must be converted before workspace archival.

This is a local backup/restore format, not a cryptographically signed approval
transfer or sandbox. Restored approval metadata has the same trusted-local-file
boundary as approval files on disk. Recheck restored drafts before publication
when runtime/dependencies or private variables differ. Checkpoint credentials
remain excluded by their normal storage policy; source/history can still contain
user-entered sensitive text. Active agent requests and direct external writers
should be stopped before a full workspace backup; the gateway worker fence does
not pause unrelated console clients or external programs.

## Declarative migrations

`database/schema.json` remains additive. Existing table changes use a separate
versioned `database/migration.json` plan. Supported operations:

- `add_column`: `table`, `column` with the existing declarative column schema.
  Primary/unique constraints require a rebuild.
- `drop_column`: `table`, `name`.
- `rename_column`: `table`, `name`, `new_name`.
- `rename_table`: `table`, `new_name`.
- `drop_table`: `table`.
- `rebuild_table`: `table`, complete matching `definition`, and explicit
  `copy_columns` mapping destination column names to source column names.

Names are quoted simple identifiers, never expressions or supplied SQL. Rebuilds
copy mapped columns, drop the old table, then rename the replacement. Old indexes
and triggers are removed, not guessed/recreated. New unmapped columns use defaults
or NULL; constraints can reject incompatible records. Preview lists removed SQL
objects so the user can review the loss. Raw SQL, arbitrary expressions and custom
trigger/index creation are outside this declarative migration format.

1. Read `current_schema_fingerprint` from GET `/data-baseline` (no capture required).
2. POST `/database-migrations/preview` with `expected_schema` and `operations`.
   Example operation: `{"op":"add_column","table":"books","column":{"name":"note","type":"TEXT"}}`.
   It executes on a disposable copy and returns `plan`, `plan_digest`, SQL and
   removed objects. No live data changes. MCP `preview_database_migration` performs
   the same preparation from `operations_json` without requiring hash invention.
3. Save the exact returned `plan` as `database/migration.json` in the task's draft.
   Update handlers/tests and additive plans to describe the final compatible schema.
   Validate and test the final draft revision. Disposable checks apply the migration
   first; a database already matching its target is not migrated again.
4. POST `/database-migrations/apply` through console/gateway with `draft_id`,
   `expected_revision` (checked draft hash), `expected_schema` (plan's from_schema),
   `expected_plan_digest`, and `confirm: true`. The draft base must be current.
   This drains/stops the worker, prepares the change on a fresh copy of live data,
   checks foreign keys/integrity, durably backs up live SQLite and restores the
   prepared result through SQLite. Every migration has a retained receipt under
   `.reqlica/migrations/`. It does not approve or activate code.
5. Review/approve/activate compatible code using the existing exact-revision flow.
   Startup refuses a version whose migration target is not the live schema.

A migration must change schema. Replaying a consumed migration ID is rejected;
create a new preview for subsequent changes. Constraint/SQL/data failures during
preparation do not alter live data. Validation failures discovered before worker
shutdown leave it running; failures after shutdown leave it stopped. Plans are
bounded to 50 operations and 64 KiB. SQLite may reject operations depending on
existing references, generated columns, indexes and schema restrictions; resolve
those in a new preview rather than bypassing checks.

GET `/database-migrations` lists receipts. A `prepared` receipt means an operation
was interrupted near restore; inspect the current schema before recovery. SQLite
backup restoration is transactional, but this API does not infer business recovery
or automatically start code after an interrupted operation.

### Roll back a migration

POST `/database-migrations/rollback` with `expected_revision` set to the migration
receipt's 32-character ID, `expected_schema` equal to its current target schema,
and `confirm: true`. This restores the checksum-verified pre-migration snapshot,
including its records. **Writes made after migration are lost from live data.**
A pre-rollback safety snapshot is retained first. The worker remains stopped;
activate/start schema-compatible code explicitly. Rollback restores SQLite, not
the code pointer, external systems or arbitrary files. Concurrent console/external
database writers remain subject to the same coordination limits as data reset.

### Recover an interrupted migration receipt

POST `/database-migrations/recover` with the same receipt-ID `expected_revision`,
current `expected_schema`, and `confirm: true`. Only `prepared` receipts qualify.
If the database matches the target, recovery marks the receipt applied; if it
matches the source, recovery marks it aborted without changing data. Any other
schema requires manual investigation. The worker remains stopped. An aborted ID
is not reused; prepare a fresh preview for another attempt.

[Documentation index](../README.md) · [Project overview](../../README.md)
