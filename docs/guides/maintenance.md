# Maintenance

These are explicit management actions. Mutations require `confirm: true` and
current revision/schema/digest values. Worker-owned operations drain traffic and
leave the worker stopped; inspect results before starting compatible code.
External database writers need separate coordination.

## Relocate a project

POST `/api/projects/{project_id}/relocate` through console or gateway:

```json
{
  "expected_revision": "<active code revision>",
  "expected_directory": "/old/absolute/project",
  "directory": "/new/absolute/project",
  "confirm": true
}
```

The destination must not exist, its parent must exist, and both locations must
share a filesystem. Identity and all project state are preserved. Absolute paths
inside source/data are not rewritten. Use export/import across filesystems.

## Full workspace archives

POST `/api/projects/{project_id}/workspace-export` with `expected_revision` and
`confirm: true`. Optional `include_secrets: true` includes private environment
values; require a separate deliberate opt-in. Export drains/stops the worker.

The ZIP includes versions, drafts, checks, approvals, conversations, logs,
settings, baselines, and recovery files. Stop active agent requests and external
writers before exporting; the worker fence does not pause them.

Import raw ZIP bytes at console
`/api/projects/workspace-import?directory=/new/absolute/path`. The path must be new,
its parent must exist, and the original ID must be unregistered. Workspace import
preserves identity and does not support cloning. Use an active bundle for clones.

Limits: 32 MiB compressed, 128 MiB unpacked, 2,000 files, 1 MiB metadata.
Import validates files without executing handlers. Recheck restored drafts if
runtime, dependencies, or environment differ. Archives are sensitive even without
explicit secret export; restored approval metadata is not a signed trust guarantee.

## Declarative migrations

New tables use `database/schema.json`. Existing schema changes use
`database/migration.json`. Supported operations are `add_column`, `drop_column`,
`rename_column`, `rename_table`, `drop_table`, and `rebuild_table`.
Plans accept simple identifiers and mappings, not arbitrary SQL expressions.
Rebuilds can remove indexes/triggers; review the listed losses.

All routes below use `/api/projects/{project_id}`:

1. Read `current_schema_fingerprint` from `GET /data-baseline`.
2. POST `/database-migrations/preview` with `expected_schema` and `operations`.
   Example: `{"op":"add_column","table":"books","column":{"name":"note","type":"TEXT"}}`.
3. Save the returned plan as `database/migration.json`, update handlers/tests,
   then validate and test the final draft. Checks apply the plan on disposable data.
4. POST `/database-migrations/apply` with `draft_id`, `expected_revision`,
   `expected_schema`, `expected_plan_digest`, and `confirm: true`.
5. Review, approve, and activate compatible code separately.

Apply backs up live data, checks a prepared copy, and records a receipt under
`.reqlica/migrations/`. A plan is limited to 50 operations / 64 KiB and must
change schema. Consumed IDs cannot be replayed. Failures after shutdown leave
the worker stopped. Activation does not apply migrations automatically.

### Roll back a migration

POST `/database-migrations/rollback` with receipt ID as `expected_revision`,
current target `expected_schema`, and `confirm: true`. It restores the verified
pre-migration snapshot, including records: later writes are lost from live data.
A safety backup is retained. Code is unchanged and the worker stays stopped.

### Recover an interrupted migration receipt

`GET /database-migrations` lists receipts. For a `prepared` receipt, POST
`/database-migrations/recover` with receipt ID, current schema, and confirmation.
Matching target marks it applied; matching source marks it aborted. Other schemas
need investigation. Recovery does not start workers or invent business recovery.

[Documentation](../README.md)
