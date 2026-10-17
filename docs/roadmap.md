# Roadmap and Design

This document records the intended product direction and implementation progress.
See the [README](../README.md) for the current backend and quick start.

## Product Direction

Reqlica aims to turn feature descriptions, Markdown, plain text, or OpenAPI specs
into stateful mock backends. For example, a bookstore API could validate orders,
check stock, create an order, and reduce stock in a single transaction.

The planned workflow is:

1. Read requirements and the existing project; surface ambiguities and assumptions.
2. Plan endpoints, models, relationships, and business rules.
3. Generate Python handlers, supporting files, tests, and proposed database changes.
4. Save an unpublished draft through MCP tools.
5. Validate and test using separate data; return failures to the AI for correction.
6. Let the user inspect and edit the changes in a dashboard.
7. Activate the exact tested and user-approved revision.

OpenAPI provides routes, schemas, and documented responses, but does not supply
every business rule. Missing behavior must appear as a proposal for review.
Once an endpoint is active, requests execute Python directly without another AI
call. The draft/check/approval/activation backend and initial AI-facing MCP tools
already exist. Initial single-turn Groq generation and standalone HTML draft
review now exist; conversation memory, automatic repair, broader provider support,
and the packaged Next.js dashboard remain planned. See [generation](generation.md).

## Planned Capabilities

- AI generation from prompts, documents, and supported OpenAPI specifications.
- Dashboard review of endpoints, fields, relationships, assumptions, and source.
- Dashboard editing of Python, models, and routes before activation.
- Generated CRUD, filtering, pagination, and custom actions.
- Test-data generation with configurable counts, ranges, enums, and relationships.
- Generated validation, uniqueness/reference checks, state transitions, and transactions.
- Mock bearer-token/API-key authentication, including invalid and expired credentials.
- Failure scenarios, delays, empty responses, and failure of the next N requests.
- Per-route/test-user rate limits with `429` responses.
- Endpoint/data browsing, request/response inspection, runtime errors, and logs.
- Baseline data and scenario-state reset.
- Database migrations and recovery for schema-changing updates.
- Project moving/import, complete export bundles, and OpenAPI export.
- Configurable browser CORS behavior.
- A packaged dashboard, installation guide, working demo, and open-weight model support.

Projects should keep stable IDs and independent data, authentication, counters,
scenarios, and logs. Removing a route must not implicitly delete its records.
A complete project export should contain executable source and relevant settings,
models, migrations, and optional starting data; an OpenAPI export cannot capture
all Python business logic.

## Runtime Architecture

The console handles management APIs and eventually the dashboard. The shared
mock gateway forwards `/mock/<project-id>/*` requests to separate project workers.
Projects can use identical route paths without conflicts. Their SQLite databases
live outside published code versions, and draft execution uses disposable copies.

Generated code needs shared helpers for database access, transactions,
authentication, responses, and logging. Only the SQLite helpers are currently
implemented. A worker process is not a security sandbox; filesystem, network,
dependency, and secret-access restrictions need a separate design.

## MCP Tools

The console automatically exposes Streamable HTTP MCP at `/mcp`. Tools call the
same project operations as the console API, not duplicate their implementation.
See the [MCP guide](mcp.md) for connection details and the initial workflow.

| Tool | Purpose | Status |
|---|---|---|
| `get_project_context` | Read routes/source, live SQLite definitions/relationships, draft progress, contract status, project notes, and SDK helpers; supports focused reads. | Implemented |
| `list_drafts` | Discover and page through existing draft summaries so new sessions can resume appropriate work. | Implemented |
| `get_api_schema` | Inspect generated revision-linked OpenAPI contracts, with separate draft/published Swagger pages. | Implemented; incomplete contracts currently warn |
| `create_draft` | Create an editable project version. | Implemented |
| `upsert_endpoint` | Add or replace a handler's route definition. | Implemented |
| `write_draft_file` | Save source and tests; migration/seed workflows remain planned. | Implemented |
| `remove_endpoint` | Remove a draft route. | Implemented |
| `validate_draft` | Check syntax, imports, and route definitions. | Implemented |
| `test_draft` | Run tests with separate data. | Implemented |
| `call_draft_endpoint` | Make an isolated test request and return results. | Implemented |
| `get_draft_changes` | Present changed file names and check results for review. | Implemented |
| `activate_draft` | Publish a tested revision already approved by the user. | Planned; use CLI |
| `read_project_logs` | Inspect request history and runtime errors. | Planned |

`list_projects` is implemented for local metadata discovery. Further proposed
project-management tools are `create_project`, `open_project`,
`get_project_status`, `start_project`, and `stop_project`.

AI tools must not expose approval or approve their own output. Approval belongs
to the exact tested content; edits invalidate it. The current explicit local CLI
approval is a workflow boundary, not authentication against another local process.

## Development Order

1. CLI startup, console, project storage, and the shared mock server. Implemented.
2. A manually written endpoint in its own project worker. Implemented.
3. Persistent data, drafts, testing, and version activation. Implemented for stable schemas.
4. AI generation through MCP, starting with one simple endpoint. HTTP tools and initial single-turn Groq generation into reviewed drafts implemented; broader generation integration remains planned.
5. Stateful workflows, authentication, failures, rate limits, and inspection.
6. Reset, export/import, installation instructions, and a working demo.

## Open Decisions

- Isolation rules for generated Python, filesystem/network access, and dependencies.
- Extensions to the existing FastAPI handler format and SQLite SDK for generated code.
- Database migration/rollback rules when older code needs an older schema.
- Embedded AI model/provider; initial MCP uses Streamable HTTP on the console.
- Dashboard packaging and serving strategy for the existing Next.js scaffold.
- Supported OpenAPI features and project export format.
- License and whether supported installation paths require a container runtime.

A later release may run approved scenarios against a real test backend and offer
a CI runner.
