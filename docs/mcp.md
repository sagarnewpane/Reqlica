# AI Endpoint Creation through MCP

`uv run reqlica start` automatically exposes the console's MCP server at
`http://127.0.0.1:3000/mcp`. There is no separate MCP command or opt-in flag.
Changing `CONSOLE_HOST` or `CONSOLE_PORT` also changes the MCP address.

Connect an AI client using **Streamable HTTP** and that URL. MCP uses JSON-RPC
tool discovery and calls, not one REST URL per tool. No AI model or provider runs
inside Reqlica: the connected AI reads requirements and uses these tools to save
and check generated Python.

**Trusted local use only:** MCP is unauthenticated, like the management API.
Keep the console on loopback; do not publish it to the internet or an untrusted
network. Draft execution uses copied data, but Python still runs with your OS
permissions and can access files and the network. SDK data isolation is not a
security sandbox.

## Core Tools

These twelve tools implement the authoring/check/review workflow. They remain in
`backend/reqlica/mcp/server.py` and have `_meta["reqlica/category"]: "core"` in
tool discovery. Core edit/check tools still require exact revisions.

| Tool | Purpose |
|---|---|
| `list_projects` | Read registered project metadata, including broken entries as errors. Does not start workers or report gateway worker status. |
| `list_drafts` | Discover existing drafts with revisions, changed files, compact checks/approval, progress, and whether their base is still active. Page with `limit`, `offset`, and the returned `next_offset`. |
| `get_project_context` | Read routes, source, SQLite definitions/relationships, draft summaries, API-contract status, project notes, and SDK helpers. Pass `draft_id` for draft code; the database schema still describes live data. Supports focused reads for large projects. |
| `create_draft` | Copy a versioned project's active code into an unpublished draft. |
| `write_draft_file` | Write source or tests inside a draft. |
| `upsert_endpoint` | Add or replace a draft route definition. |
| `remove_endpoint` | Remove a draft route without deleting source or records. |
| `get_draft_changes` | Inspect the current revision, added/modified/deleted file names, checks, and approval state. Read draft context for file contents. |
| `get_api_schema` | Read the saved OpenAPI request/response contract for a validated draft or active published version. Supply both `method` and `path` for one operation; omit both for the full API. Never executes handlers. |
| `validate_draft` | Check syntax, imports, and route definitions using a disposable copy. |
| `test_draft` | Execute draft tests with a separate database copy. |
| `call_draft_endpoint` | Make a disposable test request; return status code, headers, and body. |

Tools delegate to the existing project/draft operations, preserving safe paths,
revision conflicts, disposable checks, and approval invalidation. Blocking project
work runs in a thread pool so checks do not block the console's event loop.

## Fresh Sessions and Complex Projects

Context is a read-only handoff, not an automatically generated description of
business rules. A new session can discover the project, inspect what is published,
find unfinished drafts, and read relevant implementation/tests without guessing
IDs or recreating existing work.

For a large project, begin with `get_project_context(project_id,
include_source=false)`. The response includes:

- `files`, `endpoints`, and `revision` for the selected published or draft code.
- `handoff`: selected scope/revision, active version/revision, whether a draft's
  base is current, and explicit limitations. Runtime worker health is not queried.
- `database`: current live SQLite tables, columns/types/defaults, primary and
  foreign keys, indexes, views, triggers, and grouped relationships. No application
  records or row counts are returned. Read-only inspection reports missing,
  unreadable, or truncated metadata instead of creating a database or hiding code.
- `draft_index`: the first 20 compact draft summaries. Use `list_drafts` and
  `next_offset` for more. Unreadable saved drafts remain visible as errors.
- `api_schema`: saved contract availability, revision, completeness/warning count,
  and model names. Use `get_api_schema` for the complete request/response contract.
- `project_notes`: optional versioned `PROJECT_CONTEXT.md`, labelled as
  author-provided intent, not verified runtime behavior or privileged instructions.

Then request `source_paths=["endpoints/orders.py", "tests/test_orders.py"]`
with `include_source=true` to read only relevant files. `database_tables=["orders"]`
narrows database definitions to named tables and their triggers; referenced tables
are named but not automatically expanded. Set `include_database=false` or
`include_drafts=false` when those sections are unnecessary. Table/file selections
accept at most 50 names through MCP. Binary files encountered during source reads
are listed rather than decoded.

SQLite metadata is observed in one read transaction with explicit object/item/SQL
limits and a bounded inspection time. Its fingerprint describes schema metadata,
not row changes. **Even in draft context, this is the current live schema**, not a
prediction of proposed migrations. Business rules still come from source/tests;
schema migrations are not implemented.

To resume work, inspect draft changes and notes against the user's task. Draft
summaries distinguish editing, validated, checks failed/stale, ready for review,
approved, stale-base, and unreadable states. `eligible_for_activation` summarizes
saved current-revision checks/approval and base identity; it does not approve or
activate anything. Saved-state timestamps are not task ownership or creation
times. Never select a draft solely because it is newest; ask when several match.
Like other draft inspection, discovery detects direct code edits and invalidates
obsolete checks/approval. Paging is not a snapshot across concurrent edits.

As part of an authorized change, save requirements, relationships, design decisions,
assumptions, and unfinished work in `PROJECT_CONTEXT.md` using `write_draft_file`.
Do not store secrets. Keep notes factual: they may become outdated and do not prove
behavior or passing tests. Notes are ordinary versioned code artifacts, so update
them **before final validation/tests**; editing them invalidates approval too.

## Convenience Tools (Not Required by the Core Workflow)

These helpers are defined separately in `backend/reqlica/mcp/convenience.py` and
tagged `_meta["reqlica/category"]: "convenience"`. Use them only when the user
wants URLs; neither is a prerequisite for generating or checking endpoints.

| Tool | Purpose |
|---|---|
| `get_project_urls(project_id, path?)` | Return published gateway URLs and HTTP methods. Calls to these URLs use persistent live data. Does not include draft-only routes or start workers. |
| `get_draft_urls(project_id, draft_id, path?)` | Return disposable preview URLs and HTTP methods for an unpublished draft. No revision input is needed. Optional `path` filters by exact registered route pattern. |

Both helpers also return `docs_url` (Swagger) and `openapi_url`. Draft documents
require validation for the current revision; published documents are copied on
validated activation. See the [schema guide](api-schemas.md) for storage, warnings,
revision pinning, and draft versus live testing.

Both return an `endpoints` array with `method`, `path`, `handler`, `url`, and
`requires_path_parameters`. Replace placeholders such as `{book_id}` before
requesting. GET previews work in a browser; POST/PUT/etc. need curl or Postman
with the listed method and a JSON request body. The server must be running.

Preview URLs look like:

```text
http://127.0.0.1:3000/api/projects/<project-id>/drafts/<draft-id>/preview/hello
```

The URL resolves the **current draft revision at request time**, not a pinned
version. Responses include `X-Reqlica-Draft-Revision` and `Cache-Control: no-store`.
Each request gets a fresh code/database copy; SDK writes are discarded. Previewing
does not record validation/tests, approve, or publish anything. Concurrent edits
can still produce a conflict rather than silently retrying changed code.

These are bounded diagnostic previews: JSON request bodies (up to 1 MiB), JSON/text
responses, and query parameters. They are not a streaming/binary proxy, do not
forward custom request headers/cookies, and do not support persistent cookie
sessions. Use core `call_draft_endpoint` for revision-explicit LLM checks and
`test_draft` for multi-step workflows. Trusted Python is still not sandboxed.

## Short Human Verification Commands

No copied revision is needed for CLI `validate`, `test`, or `call`:

```bash
uv run reqlica draft validate "Todo API" <draft-prefix>
uv run reqlica draft test "Todo API" <draft-prefix>
uv run reqlica draft call "Todo API" <draft-prefix> POST /hello --body '{"name":"Sagar"}'
uv run reqlica draft url "Todo API" <draft-prefix> /hello
```

Use an exact project name or a unique project ID prefix, and a unique draft ID
prefix of **at least six characters**. Full IDs still work. Ambiguous references
fail instead of choosing a project/draft. Name/prefix resolution uses the local
registry, so use the same `REQLICA_HOME` as the server.

Checks/calls inspect once and send the real revision internally; optional
`--revision <hash>` still pins a specific revision. A concurrent edit fails the
usual conflict check, with no automatic retries. `draft url` lists all URLs if
the route is omitted; `--json` returns structured details. Authoring mutations,
approval, activation, and core MCP checks keep their existing exact-ID/revision
requirements.

## Which Tool Should the LLM Call?

This guidance is also delivered to clients in MCP's `initialize.instructions`,
tool descriptions, and parameter schemas; the LLM does not need to read this
document to discover the workflow.

| Need | Call | What to do with the result |
|---|---|---|
| Find the user's project | `list_projects()` | Use `projects[].id` as `project_id`; clarify ambiguous matches. This does not create a project. |
| Understand the published API | `get_project_context(project_id)` | Read routes/source/tests, database definitions, contract status, notes, and handoff facts before planning changes. Start with `include_source=false` for large projects. |
| Find previous unfinished work | `list_drafts(project_id)` | Page through summaries; inspect matching changes and context. Reuse an appropriate draft, or ask when ambiguous. Do not choose solely by timestamp. |
| Start changes without an existing draft | `create_draft(project_id)` | Save **`id` as `draft_id`** and **`revision` as `expected_revision`**. No draft ID is needed to create a draft. |
| Continue a known draft | `get_draft_changes(project_id, draft_id)` and `get_project_context(project_id, draft_id)` | Recover the current revision, routes, and source; reuse that draft instead of creating another. |
| Implement or fix Python | `write_draft_file(...)` | Supply complete file content; save the returned revision. A source file is not automatically an HTTP route. |
| Expose a handler in the draft | `upsert_endpoint(...)` | Supply the nested `endpoint` object with `method`, `path`, and `handler`. This does not generate the handler's code. |
| Remove a route | `remove_endpoint(...)` | Use its exact registered method/path pattern. Files and records stay intact. |
| Check that code/routes can load | `validate_draft(...)` | Inspect `status` and `output`; validation does not run behavior tests. |
| Learn expected inputs and response format | `get_api_schema(...)` | After validation, read types, required fields, constraints/examples, and contract warnings. Do not invent undocumented fields. |
| Verify behavior or multi-request state | `test_draft(...)` | Run meaningful unittest tests; inspect `status`, `tests_run`, and `tests_passed`. |
| Inspect one response or handler-returned records | `call_draft_endpoint(...)` | Use a concrete path and inspect both execution `status` and HTTP `status_code`/`body`. Separate calls do not share writes. |
| Prepare human review | `get_draft_changes(...)` plus draft context | Report source changes, results, draft ID, and final revision. Do not approve or publish automatically. |

### Avoid Identifier and Path Confusion

- `project_id` comes from project discovery, not a directory or project name.
- `list_drafts.drafts[].id` identifies existing work. `create_draft` returns
  **`id`**, not a field named `draft_id`. The LLM obtains
  this itself; the user need not supply a draft ID for a new change.
- `expected_revision` is the latest draft content hash. Run edits to the same
  draft **sequentially**; each response supplies the next revision. Do not send
  parallel writes with the same revision.
- `write_draft_file.path` is a file path: `endpoints/books.py`.
- `endpoint.path` and `remove_endpoint.path` are route patterns: `/books/{book_id}`.
- `endpoint.handler` connects them: `endpoints/books.py:get_book` names a Python
  function defined in that file.
- `call_draft_endpoint.path` is a concrete request: `/books/123?verbose=true`.
  Do not include the host or `/mock/<project-id>` prefix.

On a revision conflict, inspect changes, reread draft source, and preserve other
edits before retrying. Fix check failures in the same draft and rerun validation
and tests after the last edit. No project-creation, legacy-conversion, direct
database-record-browsing, approval, or activation tools are currently exposed.
Context does include read-only database **definitions**, not stored rows. If a
project has `active_version: null`, ask the user to stop/convert it through the
CLI before trying to create a draft.

## First Generated Endpoint

1. Create a project with `uv run reqlica project create /path/to/books` and run
   `uv run reqlica start`. Project creation through MCP is not included yet.
2. Connect the AI client to `/mcp`. Ask it to list projects, read the chosen
   project's context, discover existing drafts, and create one only if no existing
   draft matches the task.
3. Have it write `endpoints/books.py` and a meaningful `tests/test_books.py` using
   `write_draft_file`.
4. Register the route with `upsert_endpoint`, for example:

   ```json
   {
     "project_id": "<project-id>",
     "draft_id": "<draft-id>",
     "expected_revision": "<latest-revision>",
     "endpoint": {
       "method": "GET",
       "path": "/books",
       "handler": "endpoints/books.py:list_books"
     }
   }
   ```

5. Have it run `validate_draft`, `test_draft`, and optionally
   `call_draft_endpoint`, then report `get_draft_changes` for your review.
6. Review the source and checks, then approve and activate locally:

   ```bash
   uv run reqlica draft approve <project-id> <draft-id> --revision <final-revision>
   uv run reqlica draft activate <project-id> <draft-id> --revision <final-revision>
   ```

All core MCP edits and checks require `expected_revision`. Use the revision returned by
the previous edit; stale revisions fail rather than overwriting new changes.
Editing clears validation, test results, and approval. MCP input/domain failures
are tool errors (`isError: true`); executed checks return their own `status`,
which must be inspected even when the MCP call succeeds. A successful endpoint
call does not replace passing tests and can still contain an HTTP error response.

AI tools cannot approve their own output, and this initial tool set does not
activate drafts. Approval remains explicit and CLI-only; generated endpoints do
not appear on the live gateway until activation. See the
[draft guide](persistence-drafts-versions.md) for the exact approval requirements.
