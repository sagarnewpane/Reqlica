# MCP tools for other agents

Reqlica exposes its tools through MCP so external agents can inspect projects,
author APIs, run checks, and prepare data. Use an MCP-capable client, such as
OpenCode, with a model that supports tool calls. The client manages its own model
credentials; it does not have to use a built-in Project AI provider.

Start Reqlica with `uv run reqlica start`, then configure the client with:

```text
Transport: Streamable HTTP
URL: http://127.0.0.1:3000/mcp
```

The URL follows `CONSOLE_HOST` and `CONSOLE_PORT`. There is no separate MCP startup
command. Keep it local: the endpoint is unauthenticated.

## Core tools

| Task                          | Tools                                                        |
| ----------------------------- | ------------------------------------------------------------ |
| Discover projects and drafts  | `list_projects`, `list_drafts`                               |
| Read code, schema, and checks | `get_project_context`, `get_draft_changes`, `get_api_schema` |
| Create/edit a draft           | `create_draft`, `write_draft_file`                           |
| Register/remove routes        | `upsert_endpoint`, `remove_endpoint`                         |
| Verify a draft                | `validate_draft`, `test_draft`, `call_draft_endpoint`        |
| Prepare records               | `get_mock_data_context`, `preview_mock_data`                 |

Model output is processed as tool arguments and draft content. See the
[AI disclosure](../ai-usage.md) for exact consuming functions.

## First generated endpoint

1. Create a project in the dashboard or CLI.
2. Ask the agent to discover it and read its context and existing drafts.
3. Reuse a matching draft, or call `create_draft` and save its `id` and `revision`.
4. Write a handler and tests with `write_draft_file`, then register the route:

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

5. Run `validate_draft` and `test_draft`. Inspect their status, output, and test counts.
6. Review the source, approve it, and activate it through the dashboard or CLI.

MCP has no approval or activation tools. A successful tool call does not mean a
check passed or an endpoint returned 2xx; inspect the result fields.

## Revisions and context

Use actual discovered IDs. `create_draft` returns `id`, not `draft_id`.
Each edit returns a new revision; use it on the next edit/check. Writes must be
sequential. A conflict requires rereading current state, not replaying blindly.

`write_draft_file` takes complete file content and a code-relative path such as
`endpoints/books.py`. A route is a separate definition, such as `/books/{id}`.
A test call uses a concrete path such as `/books/1` without a host or mock prefix.

For large projects, start with `get_project_context(include_source=false)`, then
select `source_paths` and `database_tables`. Database context describes live
schema, even for drafts; it does not include records or applied draft proposals.
Use `PROJECT_CONTEXT.md` for project notes, and update it before final checks.

## Convenience tools (not required by the core workflow)

`get_project_urls` returns live endpoint, Swagger, and OpenAPI URLs.
`get_draft_urls` returns disposable preview URLs. Replace path placeholders.
Draft previews use a fresh database copy per request; writes do not carry over.
They support bounded JSON/text diagnostics, not streaming, binary uploads, or
persistent cookie sessions. Use behavior tests for multi-request flows.

CLI checks also accept project names and unique ID prefixes of at least six characters:

```bash
uv run reqlica draft validate "Book API" DRAFT_PREFIX
uv run reqlica draft test "Book API" DRAFT_PREFIX
uv run reqlica draft url "Book API" DRAFT_PREFIX
```

## Reviewed package installation

External clients can use `inspect_runtime_packages`, `preview_package_install`,
`get_package_install_job`, and `install_package`.
Inspect the environment, preview resolution, and show the user every package/version.
Install only after explicit authorization using the reviewed job, plan digest,
and `confirm=true`. Read the job after a lost response; do not resubmit blindly.

The installer affects the shared environment and invalidates draft checks.
Only reviewed wheels are accepted. Built-in Project AI cannot install packages.
See [installation](installation.md#install-missing-libraries-from-the-dashboard).

## Project operations

| Area               | Tools                                                                                             |
| ------------------ | ------------------------------------------------------------------------------------------------- |
| Traffic and CORS   | `get_traffic_policies`, `set_traffic_policy`, `reset_scenario_counters`                           |
| Workers            | `get_worker_status`, `control_project_worker`                                                     |
| Data baselines     | `get_data_baseline`, `capture_data_baseline`, `reset_project_data`                                |
| Migrations         | `preview_database_migration`, `list_database_migrations`                                          |
| Archives and moves | `inspect_project_archive`, `export_project_archive`, `import_project_archive`, `relocate_project` |
| Logs               | `read_project_logs`                                                                               |

Live writes require explicit human authorization, current revisions/digests, and
confirmation. `confirm=true` is a caller assertion; the client must enforce approval.
Start/restart executes published code. Reset discards later data and leaves the
worker stopped. Archive paths refer to the Reqlica server, not the client machine.
Inspect archives and their digest before import. Workspace secret exports need a
separate explicit opt-in. See [maintenance](maintenance.md) and [reset](reset.md).

Built-in Project AI has fewer tools: it can inspect project operations but cannot
change live traffic, control workers, or use archive paths. Draft edit/execution
permissions do not grant these operations.

Project creation/deletion, secret writes, migration apply/rollback, live record
insertion, and capture settings use management APIs or the dashboard.

[Documentation](../README.md)
