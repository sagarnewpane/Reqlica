# Data baselines and scenario reset

Data reset restores a saved SQLite snapshot; scenario reset rearms failure and
rate-limit counters. Both are explicit management actions. External MCP clients
can use the [confirmed baseline/reset tools](mcp.md#project-operations); built-in
Project AI can inspect baseline metadata but cannot capture or reset it.

## Save a data baseline

1. Call `GET /api/projects/{project_id}/data-baseline` on the console. It returns
   `revision` (all zeroes before the first baseline), optional `baseline` metadata,
   and `current_schema_fingerprint`.
2. Call `PUT` at the same path with:

```json
{
  "expected_revision": "<revision from GET>",
  "confirm": true
}
```

This captures a consistent SQLite backup including committed WAL data. Existing
workers can continue running. No application records are returned or changed.
Saving again replaces the current baseline pointer and generates a new revision;
older snapshot files are retained. Stale revisions return 409. Reads and captures
use `Cache-Control: no-store`.

Baselines contain the entire SQLite database, including schema, records and
sequence counters. To use an empty starting dataset, explicitly prepare that
state and capture it as the baseline. Baselines do not contain Python globals,
files outside SQLite, external services, environment variables or scenario state.

## Restore data

Read baseline metadata again, then call
`POST /api/projects/{project_id}/data-reset` through the console or gateway:

```json
{
  "expected_revision": "<current baseline revision>",
  "expected_schema": "<current_schema_fingerprint from GET>",
  "confirm": true
}
```

This replaces current application records with the baseline. `confirm` must be
JSON boolean true; numbers and strings are rejected. Missing/modified baselines,
stale revisions and mismatched schemas are rejected before restoration. Schema
changes, including newly added tables, require a compatible new baseline; reset
is not a schema migration/rollback mechanism.

The gateway fences new project traffic, waits up to five seconds for existing
requests to drain, then stops the worker. Other projects remain available. A drain
timeout leaves data and the running worker intact. Under the project/database
locks, reset rechecks compatibility, saves and fsyncs a private pre-reset snapshot,
then restores through SQLite's backup API. It does not replace database files by
copying raw bytes over live connections.

A successful response includes `status: "reset"`, the baseline revision,
`backup` (project-relative recovery snapshot), and `worker_status: "stopped"`.
The worker remains stopped after successful reset or a restore-stage failure.
Explicitly start it through `POST /api/projects/{project_id}/start` on the gateway
or `reqlica project start <project-id>`. Startup can run application import-time
code; leaving it stopped lets the user control that step. The stop flag follows
the existing gateway lifecycle and does not persist across a gateway restart.

If restore fails, retain the returned/reported backup directory and inspect local
state before restarting. Pre-reset snapshots are in `.reqlica/database-backups/`.
Baseline files are in `.reqlica/data-baselines/` with metadata in
`.reqlica/data-baseline.json`. Files are created privately. Snapshots are retained
without automatic pruning; repeated captures/resets consume disk space. They may
contain sensitive application data.

Authentication/environment settings, code versions, draft checks/approval,
scenario rules/counters and inspection history are unchanged. Direct external
SQLite writers are outside Reqlica's worker coordination; stop those separately.
Missing projects return 404, invalid request bodies 422, stale/incompatible state
409, and lifecycle/restore availability failures 503.

## Reset scenario counters

Read `GET /api/projects/{project_id}/scenarios`, then call the console:
`POST /api/projects/{project_id}/scenarios/reset` with:

```json
{
  "expected_revision": "<scenario revision>",
  "confirm": true
}
```

This preserves every rule, restores finite failure counts, clears all rate-limit
windows and generates a new scenario revision. No worker restart is needed.
Updates serialize with quota/failure reservations. In-flight requests keep their
already selected actions; subsequent reservations use reset counters. Stale
configuration revisions return 409. Application SQLite data, auth and inspection
history are unchanged. Data reset and scenario reset are separate operations;
there is no atomic combined reset.

[Documentation index](../README.md) · [Project overview](../../README.md)
