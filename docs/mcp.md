# AI Endpoint Creation through MCP

`uv run reqlica start` automatically exposes the console's MCP server at
`http://127.0.0.1:3000/mcp`. There is no separate MCP command or opt-in flag.
Changing `CONSOLE_HOST` or `CONSOLE_PORT` also changes the MCP address.

Connect an AI client using **Streamable HTTP** and that URL. MCP uses JSON-RPC
tool discovery and calls, not one REST URL per tool. In this MCP workflow, the
connected AI reads requirements and uses tools to save and check generated Python.
The standalone dashboard uses the same MCP interface through **Project AI**.
The old generation, clarification, separate chat, and repair engines have been
removed. See [Project AI](generation.md) for API and migration details.

## Project AI inside the dashboard

1. Add a provider profile with a model supporting Chat Completions function tools
   (Groq, OpenAI, or an OpenAI-compatible API).
2. Open **Project AI** and use the composer for questions, specifications, API
   creation/changes, repairs, or synthetic data. Attach a JSON/YAML/Markdown/text
   specification or paste one. Message plus attachment must fit 32,768 characters.
3. Expand **Conversation setup** to choose published context or an existing draft.
   Existing editable drafts must use the current published base. After activation,
   start new work from published code. Older drafts matching published code roll
   over safely; diverged old-base drafts still require manual review.
4. **Allow draft edits when requested** permits source changes. Disable it for
   server-enforced read-only questions and data previews. Legacy projects support
   read-only conversations; convert to versioned storage for draft editing.
   **Allow validation, tests, and disposable requests** separately permits Python
   execution with your OS permissions. Checks are disabled by default.
5. Send the first message after reviewing the provider/context disclosure.
   Use the same composer for clarification and follow-up messages. Setup is fixed
   per conversation; **New conversation** lets you change it. Follow-ups retain
   one assigned draft, complete tool history, and the remaining request budget.
6. Expand tool actions, inspect revision-linked checks, and use **Review draft**
   for source review, remaining manual checks, approval, and activation.
   **Pause after this step** stops future steps after the current request finishes.

Endpoint modification and failed-check repair buttons prefill Project AI with the
route or draft context. Mock-data preparation also prefills a conversation. These
entry points do not call the model until you send the message.

For mock data, `get_mock_data_context` reads live definitions and published app
contracts without records or execution. `preview_mock_data` validates JSON fixtures,
one-table SQLite batches, or related records on disposable copies using the manual
screen's application-format checks. Unsupported hashes/encrypted/custom formats
remain refused. Related rows use references such as `{"$ref":"users.0.id"}`.
**Review mock data** transfers a preview to the manual data screen for editing and
fresh validation. Insertion remains separately confirmed; there is no live-insert
MCP tool. A preview never creates database tables.

Saved conversations appear under **Conversations**; earlier browser chats remain
viewable as read-only archives. Drafts created by the retired generation engine
remain available in **Drafts & checks**. Old generation jobs cannot resume. Replies are idempotent after a
lost acknowledgement. Outside code changes or newer publication stop continuation
for review; follow-ups cannot reset conflicts, permissions, or exhausted budgets.

AI replies render basic Markdown (paragraphs, emphasis, lists and code); raw HTML
remains escaped. Tool errors show fixed, actionable explanations when their cause
is known, including missing/stale API contracts that need current validation.

Each browser request advances one model response or one MCP tool call. Tool
arguments are checked against the selected project, one assigned draft, the
latest revision, draft-edit permission and execution permission before being passed to MCP. The
agent cannot list other projects/drafts, approve, activate, install packages,
apply live database setup or make live gateway calls. Source writes are limited
to canonical Python files in `endpoints/`, `models/`, `tests/`, the additive
`database/schema.json` plan and `PROJECT_CONTEXT.md`. A tool error is returned to
the model; a completed tool can still report a failing check.

Private checkpoints in `<project>/.reqlica/agent-runs/` retain the request, model
settings, transcript, pending tool calls, tool activity, and usage. Provider keys
are supplied per request and never stored there. Selected source and tool results
are sent to the provider; context excludes environment values, but trusted Python
can print secrets or return records in its output. Checkpoints persist across
refreshes and server restarts; reopening a build never advances it automatically.
They remain until removed locally or with the project directory.

**Conversations → Open conversation → Resume conversation** continues within the original
budget: at most 24 model requests and 64 tool calls, with a 512 KiB model-context
limit. Failed provider requests consume budget and require an explicit resume.
Edits outside the build or a changed published base stop it for review. A process
interrupted during an in-flight step is never replayed automatically, because a
draft write may already have committed. Inspect existing drafts and start a new
build targeting the appropriate draft. Closing the tab also stops future steps;
the current server request may still finish. Model support and quality depend on
the selected provider; there is no fallback to text-generated tool calls.
OpenRouter builds check the model's advertised tool support and only send optional
tool controls it supports. This avoids routing failures from requiring an
unsupported `parallel_tool_calls` option. Unknown compatible providers receive
the tool definitions without optional parallel/choice controls; the backend still
executes returned tool calls sequentially. HTTP 404 errors distinguish known
OpenRouter parameter/privacy routing failures from model or base-URL problems.

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
live schema changes require the explicit [migration management workflow](maintenance.md).

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

Schema changes can be prepared with read-only `preview_database_migration`. Save
its returned plan in the task draft, run checks, and leave live apply/rollback to
the explicit management APIs. No AI approval or activation tools are exposed.


## Reviewed package installation

External MCP clients now have four package tools:

| Tool | Purpose |
| --- | --- |
| `inspect_runtime_packages` | Missing imports, target environment, environment revision, recent job IDs. Full inventory is opt-in. |
| `preview_package_install` | Resolve a verified distribution and optional exact version; downloads metadata but installs nothing. |
| `get_package_install_job` | Read exact resolved versions, plan digest, progress, or saved result. Output is opt-in. |
| `install_package` | Install the reviewed job after explicit human authorization, with its exact plan digest and `confirm=true`. |

Inspect first, pass the returned environment revision to preview, and poll the
returned job ID until status is `review`. Show the human the target Python
environment and every package/version marked `install`. Only after approval call
`install_package`; poll the same saved job to completion. Lost acknowledgements
must be followed by job inspection, never automatic resubmission. Plans expire
and refuse changed environments or altered lock contents.

The same installer backs the dashboard and MCP: writable shared virtualenv,
matching gateway/console environments, existing-version constraints, hash-pinned
wheels, no source builds, no arbitrary URLs/flags, and no upgrades/removals. Installs
invalidate draft checks and approval. Restart affected APIs and rerun checks.
No real package is installed merely by tool discovery or endpoint generation.
`confirm=true` is a caller assertion of human approval, not an independent human
verification mechanism; use a trusted local MCP client with tool approvals.

The built-in **Project AI** allowlist still excludes installation tools: enabling
draft edits or disposable execution does not grant environment modification.
Use an external MCP client with explicit human approval, or the package panel.
See [installation details](installation.md#install-missing-libraries-from-the-html-dashboard)
for persistence and reproducibility limits.

## Project operations

These thirteen tools reuse the existing gateway/domain operations; they are tagged
`core` and require no additional MCP server. They make the following tasks useful
from an external client:

| Capability | User benefit | Tools |
| --- | --- | --- |
| Traffic policies | Reproduce latency, failures, empty responses and quotas; fix browser CORS settings. | `get_traffic_policies`, `set_traffic_policy`, `reset_scenario_counters` |
| Workers | Diagnose worker health and explicitly start/stop/restart a published API. | `get_worker_status`, `control_project_worker` |
| Baselines/reset | Capture a known dataset and repeat tests against the same starting state. | `get_data_baseline`, `capture_data_baseline`, `reset_project_data` |
| Migration history | Explain applied, rolled-back or interrupted changes using saved receipts. | `list_database_migrations` |
| Transfers | Clone an active project, archive a workspace, restore it, or move its directory. | `inspect_project_archive`, `export_project_archive`, `import_project_archive`, `relocate_project` |

All writes require explicit human authorization of the operation/target and
`confirm=true`. This is a caller assertion, not a separate human verification
mechanism; use a trusted local client with human tool approvals. Diagnosis or
endpoint generation alone does not authorize live changes. Never automatically
replay a mutation after a timeout or lost acknowledgement: inspect the worker,
baseline, policies, registration or destination file first. Restart the console
and gateway after upgrading; reconnect MCP clients to refresh tool discovery.

`get_traffic_policies` returns independent revisions for scenarios, CORS and
authentication metadata. It never returns credential values/hashes. For
`set_traffic_policy`, send `policy=scenarios|cors`, that policy's revision, and a
replacement `settings_json` object. Scenario JSON contains `rules`; omit output-only
`remaining_failures`. Replacing scenarios resets all counters, and `rules: []`
disables them. CORS JSON uses the fields returned by inspection, omitting
`project_id`/`revision`; omitted fields use defaults. Authentication writes remain
in the dashboard to keep credentials out of tool arguments.

For workers, first read `get_worker_status`: it returns gateway status, directory,
and current `active_revision` without starting Python. Control pins that published
code hash and accepts `start|stop|restart`. Start/restart executes trusted Python
with OS/network access; stop can interrupt requests and prevents lazy startup.
Control never activates drafts. Read status after any failure, including restart
failure (which can leave the worker stopped). A gateway outage is reported as an
error, never inferred as stopped status.

For data, read `get_data_baseline`. Capture pins its 32-character baseline revision
(all zeros before first capture); reset additionally pins the returned current
schema fingerprint. Reset discards SQLite changes since capture, retains a
pre-reset safety backup, and leaves the worker stopped. Capture changes the
baseline pointer and retains older snapshots; it does not stop workers. Scenario
counter reset is separate and uses the scenario revision. See [reset details](reset.md).

Migration history is read-only and paginated (`limit=20`, maximum 100, `offset`,
`next_offset`). Only the requested page is loaded. Receipts contain schema/draft
pins, phase and backup paths, not database records. Ordering is by receipt filename,
not execution time; pages may shift during concurrent maintenance. Existing
`preview_database_migration` prepares changes; live apply/rollback/recovery remain
in the explicit [maintenance workflow](maintenance.md).

Transfers use absolute paths on the **Reqlica server**, not the MCP client's
computer. Export requires a new ZIP file outside registered project directories
and pins `active_revision` from worker inspection. Files are created private
(`0600`), never overwritten. `kind=active` exports portable source/data/policies
and environment names; `kind=workspace` drains/stops the worker and includes
drafts/history. Only workspace exports accept `include_secrets=true`, after a
separate explicit human opt-in to include private environment values. Source and
SQLite may contain sensitive data even when private environment export is disabled.

Inspect the file with `inspect_project_archive` to review its format, identity,
secret-export flag, size and SHA-256 digest; this metadata inspection does not
fully validate the archive. Import pins that digest and a new absolute destination;
it fully validates paths, checksums, SQLite and configuration, and executes no
handlers. Use `clone=true` for a new ID from an active bundle. Workspace restore
preserves identity/name and requires the original ID to be unregistered; it cannot
clone. Imports are idle registrations; later live requests can lazily start Python.
ZIP files never appear in tool responses. Remote clients must arrange file transfer
separately; HTTP upload/download remains available in the dashboard. See
[transfer details](project-transfer.md).

`relocate_project` pins both current directory and active revision. It moves the
entire workspace on the same filesystem and leaves the worker stopped; use
export/import across filesystems. No replacement or overlapping destination.

Built-in **Project AI** gets only the four project-scoped read tools:
`get_traffic_policies`, `get_worker_status`, `get_data_baseline`, and
`list_database_migrations`. Live writes, archive paths and lifecycle controls are
excluded regardless of draft-edit/execution permissions. External MCP clients
can use the confirmed write tools. Approval and activation remain outside MCP.

## Remaining API-only capabilities

Project creation/conversion/deletion, secret writes, migration apply/rollback/
recovery, draft housekeeping, live mock-data insertion, and inspection capture
settings still use their explicit management APIs/dashboard. Names and redacted
inspection remain discoverable; adding tools for these is separate work.
