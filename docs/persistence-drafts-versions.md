# Persistence, Drafts, and Versions

This is the implemented manual backend workflow, not an AI generator or dashboard.
Projects have a stable ID, code snapshots, and one live SQLite database. The CLI
and console delegate editing, checks, and approval requirements to the project
domain API; worker lifecycle changes belong to the mock gateway.

## Configuration and Trust

Run `uv sync --locked`, configure the repository-root `.env`, then run
`uv run reqlica start`. Defaults are console `127.0.0.1:3000` and mock gateway
`127.0.0.1:4000`. Set `REQLICA_HOME` consistently for servers and local approval;
the registry defaults to `~/.reqlica/projects/`. Exported environment variables
take precedence over `.env`. The CLI maps wildcard bind hosts to loopback and
brackets IPv6 hosts. HTTP clients bypass environment proxies.

Keep both servers on loopback. These are local unauthenticated management APIs,
not authorization boundaries. Endpoint imports, validation, tests, startup, and
draft calls execute trusted Python with the user's permissions. Separate workers,
subprocess timeouts, path containment, and disposable databases do not prevent
malicious code from accessing the filesystem, live database, network, or secrets.
Only run code you trust. SQLite snapshots can contain sensitive live records.
The current process/locking implementation requires a POSIX system (macOS or
Linux); Windows is not supported by this backend workflow.

Approval is a deliberate human workflow step, not protection against another
local process acting as the same user. There is no approval HTTP endpoint.
Future agents and MCP tools must not expose approval or auto-approve changes.

## Storage

```text
project/
  project.json                    # id, name, active_version
  data.sqlite                     # Shared live SQLite database
  versions/<version-id>/           # Published code snapshot
    manifest.json
    endpoints/hello.py
    tests/test_hello.py
  drafts/<draft-id>/               # Editable code only
  .reqlica/project.lock            # Project serialization
  .reqlica/runtime.lock            # Worker/recovery database exclusion
  .reqlica/drafts/<draft-id>.json   # Revision, base_version, checks, approval
```

Project, draft, and version IDs are 32 lowercase hexadecimal characters. A
revision is a 64-character SHA-256 digest of sorted relative filenames and file
contents, not a counter or modification time. Python caches are excluded. Inspect
returns `id`, `revision`, `base_version`, `files`, `changes` (`added`, `modified`,
`deleted`), `validation`, `test`, and `approval`. Results are returned unchanged,
including nested endpoint-call bodies and check output.

Do not edit published versions, state JSON, `project.json`, or the registry by
hand. Code files must be regular files/directories, not symlinks. File operations
reject absolute paths, traversal, and reserved control/database paths. Console
reads inspect/digest the draft under its project lock and use contained file reads.
The locks serialize cooperating Reqlica operations; direct filesystem writers
are not required to honor those locks, so avoid concurrent external edits.

## Manual Workflow

Create a project with `uv run reqlica project create /path/to/books --name Books`.
It includes a working `GET /hello` handler and a real starter `unittest` test that
imports `hello` from `endpoints.hello`. Use the printed project ID below.

```bash
uv run reqlica draft create <project-id> --json
uv run reqlica draft list <project-id> --json
uv run reqlica draft inspect <project-id> <draft-id> --json
uv run reqlica draft write <project-id> <draft-id> endpoints/books.py \
  --file /path/to/local/books.py --revision <current-revision> --json
uv run reqlica draft upsert <project-id> <draft-id> GET /books \
  endpoints/books.py:list_books --revision <revision-after-write> --json
uv run reqlica draft write <project-id> <draft-id> tests/test_books.py \
  --file /path/to/local/test_books.py --revision <revision-after-upsert> --json
uv run reqlica draft inspect <project-id> <draft-id> --json
uv run reqlica draft validate <project-id> <draft-id> --revision <final-revision> --json
uv run reqlica draft test <project-id> <draft-id> --revision <final-revision> --json
uv run reqlica draft call <project-id> <draft-id> GET /books --revision <final-revision> --json
uv run reqlica draft inspect <project-id> <draft-id>
uv run reqlica draft approve <project-id> <draft-id> --revision <final-revision>
uv run reqlica draft activate <project-id> <draft-id> --revision <final-revision> --json
```

Each mutation returns a new revision. Use it for the next operation, even when
only a manifest changed. Direct edits under `drafts/<draft-id>` are detected by
the digest on inspection or the next operation; they invalidate stored results
and approval. Do not reuse an older revision after editing.

`validate` performs static checks and application loading in a disposable runner.
`test` discovers `test*.py` under the draft's `tests/` directory and must run at
least one genuinely passing test (`tests_passed`); skipped tests and expected
failures do not count. Both results must have `status: passed` and the exact
final revision before approval. A successful draft call is not a substitute for
validation or tests. Runner `status: passed` means the call executed, not that
its HTTP response was 2xx: inspect `status_code`, `headers`, and `body`.

Checks and calls use a temporary code snapshot and consistent SQLite backup of
live data, including committed WAL contents. Each operation gets its own copy;
a draft POST call's changes are discarded rather than carried into the next call
or live database. Source changes in either the original draft or the execution
snapshot invalidate results. Check output is bounded and execution has a domain timeout.
SQLite backup/restore operations also have a ten-second timeout rather than
waiting indefinitely on a locked or continuously changing database.
CLI console requests and activation forwarding allow 60 seconds; existing
project list/start/stop/convert requests allow 30 seconds.

To delete a file or route:

```bash
uv run reqlica draft delete <project-id> <draft-id> endpoints/old.py --revision <revision>
uv run reqlica draft remove <project-id> <draft-id> GET /old --revision <revision-after-delete>
```

Add tests for new behavior rather than relying only on the starter hello test.
For an endpoint using the [stateful example](#stateful-endpoints), a draft test can be:

```python
import unittest

from endpoints.books import BookInput, create_book, list_books

class BookTests(unittest.TestCase):
    def test_create_and_list(self):
        created = create_book(BookInput(title="Draft test book"))
        self.assertIn(created, list_books())
```

The runner configures SDK database access before importing project code; this
test writes only to the disposable copy when run through `draft test`.

## Explicit Approval

```bash
uv run reqlica draft approve <project-id> <draft-id> --revision <sha>
uv run reqlica draft approve <project-id> <draft-id> --revision <sha> --yes --json
```

Without `--yes`, the CLI displays the exact revision, file changes, validation,
and test results on stderr, then prompts. Only `y` or `yes` accepts; an empty
answer, negative answer, or EOF declines without recording approval and exits
nonzero. `--yes` is an explicit human approval flag, not a default or an agent
operation. It does not bypass checks. Approval calls the local domain API directly
after loading `.env`, so it works without either server when `REQLICA_HOME` is
configured correctly. The domain rechecks the expected revision at approval time.

Activation requires matching checks, at least one test, approval for that revision,
and the draft's `base_version` still being active. Editing or rerunning checks
clears approval. Activating another draft makes drafts based on the previous
version stale; create a new draft from the active version and reapply your changes.

## HTTP Routes

The console's base prefix is `/api/projects/{project_id}`:

| Method | Suffix | Request / Result |
|---|---|---|
| `POST` | `/drafts` | Create; return inspected draft. |
| `GET` | `/drafts` | Return `{"drafts": [inspected drafts]}`. |
| `GET` | `/drafts/{draft_id}` | Return inspected draft. |
| `GET` | `/drafts/{draft_id}/files?path=relative/file.py` | Return `{path, content, revision}`. |
| `PUT` | `/drafts/{draft_id}/files?path=relative/file.py` | Body `{content, expected_revision}`; return updated draft. |
| `DELETE` | `/drafts/{draft_id}/files?path=relative/file.py` | Body `{expected_revision}`; return updated draft. |
| `PUT` | `/drafts/{draft_id}/endpoints` | Body `{endpoint: {method, path, handler}, expected_revision}`. |
| `DELETE` | `/drafts/{draft_id}/endpoints` | Body `{method, path, expected_revision}`. |
| `POST` | `/drafts/{draft_id}/validate` | Body `{expected_revision}`; return check result. |
| `POST` | `/drafts/{draft_id}/test` | Body `{expected_revision}`; return test result. |
| `POST` | `/drafts/{draft_id}/call` | Body `{expected_revision, method, path, json_body?}`; return runner result. |
| `POST` | `/activate` | Body `{draft_id, expected_revision}`; forward to gateway. |
| `POST` | `/convert` | Forward legacy conversion to gateway. |

No `/approve` route exists. All mutations/checks require `expected_revision`,
except draft creation and legacy conversion. CLI `--revision` maps to this field.
Unknown projects/drafts/files return `404`, domain conflicts return `409`, invalid
request bodies return `422`, and gateway startup/connection failures return `502`.
Checks can return HTTP `200` with `failed`, `error`, or `timeout` status; inspect
the result. The CLI exits nonzero for any non-passed check/call and for HTTP
errors, emits useful stderr, and preserves the entire result with `--json`.

The mock gateway owns `POST /api/projects/{project_id}/convert` and
`POST /api/projects/{project_id}/activate`. Activation returns the existing project
payload plus `active_version`, with full endpoint URLs and the stable project ID.
The console forwards that payload/status rather than implementing lifecycle logic.
`draft activate` contacts the gateway directly. Worker statuses remain `idle`,
`running`, `stopped`, and `error`; there is no new activation status.

## Legacy Conversion

Old projects without `active_version` continue to use root-level code until
explicitly converted. Do not convert a running worker:

```bash
uv run reqlica project stop <project-id>
uv run reqlica project convert <project-id> --json
uv run reqlica project start <project-id>
```

Conversion accepts only a stopped/idle project, snapshots root code into a new
version, ensures a live database and draft directory exist, and publishes metadata
last. Original root files remain but are no longer the active code. Converting an
already versioned stopped/idle project is idempotent. Legacy code does not gain
tests automatically; add at least one test to a draft before approval/activation.

## Persistence and Recovery

Workers configure `reqlica.runtime.sdk` to use the project-level `data.sqlite`.
Use `connection()` for reads/autocommit and `transaction()` for atomic writes.
Transactions use `BEGIN IMMEDIATE`, commit on success, and roll back on exceptions;
foreign keys and a busy timeout are enabled. Parameterize SQL and keep schema
stable. The [stateful endpoint example](#stateful-endpoints) below includes
explicit one-time schema setup. There is no automatic schema initialization,
migration execution, seed/reset API, or migration-aware rollback.

Activation publishes a code snapshot and replaces the worker while retaining the
live database. New requests to that project receive `503` during activation;
already-admitted requests have five seconds to finish. A drain timeout keeps the
old worker. Other projects remain available. Activation then stops the old worker,
backs up the database, and starts the candidate against live data. This startup
check also runs for idle/stopped projects, which return to their prior state after
verification. Importable handler modules are shared with ordinary test imports;
avoid filenames that conflict with installed Python modules.

A startup failure restores the database recovery point and previous code pointer,
then restarts the old worker if it was running. Changes committed before draining
finished are included in the backup, not discarded. Candidate startup that changes
the SQLite schema or published source is rejected. External side effects cannot
be rolled back. There is no migration-aware upgrade/rollback workflow.

The durable `.reqlica/activation.json` journal and
`.reqlica/activation-backup.sqlite` allow interrupted attempts to roll back before
lazy startup. A committed journal only needs cleanup. Workers exit if their gateway
parent disappears; a separate runtime lock prevents recovery under a live worker.
A startup failure is reported as `502`; inspect project status, `active_version`,
error details, and the draft before retrying. If process cleanup or recovery itself
fails, the project remains gated and its processes remain owned for gateway
shutdown. Restart the gateway to retry recovery; do not delete its journal/backup.

On `409`, inspect the current draft and active project before acting. A stale
revision needs fresh checks and human approval; a stale base needs a new draft.
On CLI timeout or a lost HTTP response, an operation might still have completed:
inspect the project and draft before retrying rather than assuming cancellation.
After a crash, leave existing versions, database, and state in place and inspect
through the APIs. Do not manually select another active version or delete state
files as a recovery shortcut. Unreferenced snapshots may remain after interruption;
there is no automatic garbage collection or version-history rollback command.

For an operator-managed database backup/restore, stop the project's worker first
and use SQLite's backup API, not a raw copy of a possibly WAL-backed database:

```python
from reqlica.runtime.sdk import backup_database, restore_database

backup_database("/path/to/books/data.sqlite", "/safe/location/books-backup.sqlite")
# Restore only with workers stopped and a reviewed, schema-compatible backup.
restore_database("/safe/location/books-backup.sqlite", "/path/to/books/data.sqlite")
```

Backups are not code versions. Restoring a database loses changes after that
backup and does not repair an incompatible schema/code pair. Schema-changing
rollback is not supported; plan external schema changes and their recovery
separately. Keep backups outside draft/version directories.

## Stateful Endpoints

Create a stable schema explicitly while the project is stopped:

```bash
sqlite3 /path/to/books/data.sqlite \
  'CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL);'
```

Write `endpoints/books.py` in a draft using standard FastAPI-compatible handlers:

```python
from pydantic import BaseModel
from reqlica.runtime.sdk import connection, transaction


class BookInput(BaseModel):
    title: str


def list_books() -> list[dict]:
    with connection() as database:
        return [
            dict(row)
            for row in database.execute("SELECT id, title FROM books ORDER BY id")
        ]


def create_book(body: BookInput) -> dict:
    with transaction() as database:
        cursor = database.execute("INSERT INTO books (title) VALUES (?)", (body.title,))
        return {"id": cursor.lastrowid, "title": body.title}
```

Register `GET /books` with `endpoints/books.py:list_books` and `POST /books` with
`endpoints/books.py:create_book` using `draft upsert`. Add tests, validate, test,
review, approve, and activate as in the manual workflow above. Use the unchanged
schema across code versions. The worker configures SDK database access; handlers
must not override it or hardcode another database path.

Supported route methods are `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`,
`HEAD`, and `TRACE`. Routes are relative to the project's `/mock/<project-id>` URL.
Handlers support ordinary FastAPI parameters, Pydantic models, `Request`, and
`Response`. Keep existing starter behavior/tests when adding new handlers, or
update the tests deliberately when replacing that behavior.
