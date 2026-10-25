# Data baselines and reset

A data reset restores SQLite from a saved baseline. Scenario reset only rearms
traffic counters. These are separate operations.

## Save a data baseline

Read `GET /api/projects/{project_id}/data-baseline`, then `PUT` at the same path:

```json
{ "expected_revision": "<baseline revision from GET>", "confirm": true }
```

Capture saves a consistent SQLite snapshot while workers can continue. Saving
again replaces the pointer and retains old files. The baseline includes schema,
records, and sequences, not code, environment, external services, or scenario state.

## Restore data

Read baseline metadata again, then POST through console or gateway to
`/api/projects/{project_id}/data-reset`:

```json
{
  "expected_revision": "<current baseline revision>",
  "expected_schema": "<current_schema_fingerprint from GET>",
  "confirm": true
}
```

Reset discards later records. It checks schema compatibility, drains traffic,
stops the worker, saves a pre-reset backup, and restores through SQLite's backup API.
A changed schema needs a compatible baseline; reset is not a migration rollback.
The worker remains stopped. Inspect the result before explicitly starting it.

Snapshots live in `.reqlica/data-baselines/` and `.reqlica/database-backups/`;
metadata is in `.reqlica/data-baseline.json`. They can contain sensitive data and
have no automatic pruning. Stop direct external SQLite writers separately.
Code/settings and inspection history remain unchanged. Lost responses require
inspection before retrying. Stale/incompatible state returns 409.

## Reset scenario counters

Read the current scenario revision, then POST to
`/api/projects/{project_id}/scenarios/reset`:

```json
{ "expected_revision": "<scenario revision>", "confirm": true }
```

Rules stay intact, finite failures are rearmed, and quota windows clear.
No worker restart is needed. In-flight requests keep their reserved action.
There is no atomic combined data/scenario reset.

[Documentation](../README.md)
