# Drafts and versions

Each project has a stable ID, one live SQLite database, published code versions,
and editable drafts. AI, dashboard, and CLI workflows use the same revision checks.

## Storage

```text
project/
  project.json                    Identity and active version
  data.sqlite                     Live data
  versions/<version-id>/          Published source, manifest, and tests
  drafts/<draft-id>/              Editable source
  .reqlica/drafts/<draft-id>.json  Revision, checks, and approval
```

IDs are 32 lowercase hex characters. Revisions are SHA-256 content hashes.
Do not edit published snapshots, metadata, or registry files by hand.

## Manual workflow

Create a project and a draft, then save the returned IDs and revision:

```bash
uv run reqlica project create /absolute/new/books --name "Book API"
uv run reqlica draft create PROJECT_ID --json
uv run reqlica draft inspect PROJECT_ID DRAFT_ID --json
uv run reqlica draft write PROJECT_ID DRAFT_ID endpoints/books.py \
  --file /absolute/path/books.py --revision CURRENT_REVISION
uv run reqlica draft upsert PROJECT_ID DRAFT_ID GET /books \
  endpoints/books.py:list_books --revision REVISION_AFTER_WRITE
uv run reqlica draft write PROJECT_ID DRAFT_ID tests/test_books.py \
  --file /absolute/path/test_books.py --revision REVISION_AFTER_ROUTE
uv run reqlica draft validate PROJECT_ID DRAFT_ID
uv run reqlica draft test PROJECT_ID DRAFT_ID
uv run reqlica draft inspect PROJECT_ID DRAFT_ID
uv run reqlica draft approve PROJECT_ID DRAFT_ID --revision FINAL_REVISION
uv run reqlica draft activate PROJECT_ID DRAFT_ID --revision FINAL_REVISION
```

Each mutation returns the revision for the next operation. Edits invalidate
checks and approval. Direct draft-source edits are detected on inspection.

`validate`, `test`, `call`, and `url` accept project names or unique ID prefixes
of at least six characters. CLI checks can read the current revision when
`--revision` is omitted. Authoring, approval, and activation require explicit revisions.

## Checks and preview requests

Validation checks source/imports/routes and saves OpenAPI. Tests discover
`tests/test*.py` and must include at least one passing test; skips and expected
failures do not count. Changed handlers need behavior coverage.

Checks use disposable code/database copies. Each draft request has its own copy,
so one request's writes are unavailable to the next. Use one behavior test for a
stateful sequence. Runner success does not imply HTTP success; inspect status/body.

```bash
uv run reqlica draft call PROJECT_ID DRAFT_ID GET /books --json
uv run reqlica draft url PROJECT_ID DRAFT_ID
```

Preview URLs resolve the current revision and are bounded JSON/text diagnostics.
They do not replace validation or tests. See [API schemas](api-schemas.md).

## Explicit approval

In **Drafts & checks**, review source and passing checks, choose
**Approve checked revision…**, then **Activate approved revision** separately.
Approval requires the exact checked revision and a current draft base.
Editing or rerunning checks clears approval. Activating another draft makes
older-base drafts stale; reapply changes to a new draft.

CLI approval prompts unless `--yes` is supplied. The flag records deliberate
approval and does not bypass checks. MCP cannot approve or activate.

## Persistence and recovery

Activation drains requests, backs up SQLite, and starts a candidate worker.
New requests can receive 503 during this step. Startup failure restores the
recovery point and previous code pointer. External side effects cannot be undone.
Activation does not apply migrations. Do not delete recovery journals manually.

After a conflict or timeout, inspect the project and draft before retrying.
Restoring a historical code version creates a new unchecked draft; it does not
restore historical data. See [maintenance](maintenance.md) for migration rollback.

## Stateful endpoints

Use the SDK database configured by the worker:

```python
from pydantic import BaseModel
from reqlica.runtime.sdk import connection, transaction


class BookInput(BaseModel):
    title: str


def list_books() -> list[dict]:
    with connection() as db:
        return [dict(row) for row in db.execute("SELECT id, title FROM books")]


def create_book(body: BookInput) -> dict:
    with transaction() as db:
        cursor = db.execute("INSERT INTO books (title) VALUES (?)", (body.title,))
        return {"id": cursor.lastrowid, "title": body.title}
```

Create tables through [database setup](database-setup.md), not during handler
startup or requests. `transaction()` commits on success and rolls back on errors.
Use bound SQL parameters. Do not hardcode or override the SDK database path.

## Draft names, timestamps, and deletion

Names are editable and need not be unique. Renaming does not change the content
revision or invalidate checks. Old drafts may lack a recorded creation time.
Whole-draft deletion in the dashboard requires confirmation and removes its source,
checks, and schema artifacts, keeping published versions and data.
CLI `draft delete` removes a file, not the whole draft.

## HTTP routes

Console routes start with `/api/projects/{project_id}`:

- `/drafts`: create/list; `/drafts/{draft_id}`: inspect, rename, or delete.
- `/drafts/{draft_id}/files`: read/write/delete source by `path`.
- `/drafts/{draft_id}/endpoints`: add/remove routes.
- `/drafts/{draft_id}/validate`, `/test`, `/call`: run checks or a request.
- `/drafts/{draft_id}/approve`: confirm approval of `expected_revision`.
- `/drafts/{draft_id}/preview/{path}`, `/docs`, `/openapi.json`: previews/contracts.
- `/activate`: publish `{draft_id, expected_revision}` through the gateway.

Full request schemas are in `/docs`. Check endpoints can return HTTP 200 with a
failed result; inspect `status`. Revision conflicts return 409.

## Legacy conversion

Stop a legacy project's worker, run `uv run reqlica project convert PROJECT_ID`,
then start it again. Conversion snapshots existing code without adding behavior
tests. Add tests in a draft before subsequent approval and activation.

[Documentation](../README.md)
