# Reqlica

![Alt text](logo.png)

A self-hosted mock backend tool for frontend developers and QA engineers.
The goal is to generate stateful Python APIs from descriptions or API specs,
then review, test, and publish them without waiting for a production backend.

## Current Status

The backend supports manually written FastAPI handlers, independent project
workers, persistent SQLite data, and a draft/test/approval/version workflow through
the CLI and management APIs. AI clients can now create and check endpoint drafts
through the console's built-in HTTP MCP server.

The standalone HTML dashboard provides one **Project AI** conversation for questions,
specifications, endpoint changes, repairs, checks, and mock-data previews through MCP.
See the [conversation guide](docs/mcp.md#project-ai-inside-the-dashboard).
The backend includes declarative schema migrations/rollback, project relocation,
full workspace archives, baseline reset and active-project ZIP import/export.
The packaged Next.js dashboard remains separate.
See the [roadmap](docs/roadmap.md).

## Quick Start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and macOS or Linux. From the repository root:

```bash
uv sync --locked
uv run reqlica start
```

Default addresses:

- Console management API: `http://127.0.0.1:3000`
- Mock gateway: `http://127.0.0.1:4000`
- MCP for AI clients (Streamable HTTP): `http://127.0.0.1:3000/mcp`
- Interactive API reference: `/docs` on either server

Override hosts, ports, or `REQLICA_HOME` in the root `.env` or environment.
Press `Ctrl+C` to stop the servers and workers; project files and data remain.

## Create a Project

In another terminal, choose a new or empty directory:

```bash
uv run reqlica project create /path/to/books --name "Book API"
uv run reqlica project list
curl http://127.0.0.1:4000/mock/<project-id>/hello
```

Creation prints the project ID and sample endpoint URL. The first request starts
its worker automatically. Manage it with:

```bash
uv run reqlica project endpoints <project-id>
uv run reqlica project stop <project-id>
uv run reqlica project start <project-id>
```

The gateway also accepts `POST /api/projects` with
`{"directory": "/absolute/path/to/books", "name": "Book API"}` and returns the
created idle project with HTTP `201`. The standalone
`backend/reqlica/reqlicav1.html` dashboard can create and delete projects; its
**Connection & help** section includes the local server launcher.

In **Request tester**, choose a route shortcut and click **Insert example** to
fill its path/query and JSON body from saved OpenAPI examples, defaults, or types.
This never sends the request and keeps custom headers. Review sample values and
record IDs before sending. Drafts need current validation; live contracts become
available after validated activation. Missing contracts leave inputs unchanged.

**Project AI** is one conversation for understanding the project, building from an
attached/pasted specification, changing endpoints, repairing drafts, running checks,
and preparing synthetic data previews. The same composer handles clarifications
and follow-ups, keeping the assigned draft, provider, permissions, and remaining
budget. Expand **Conversation setup** to select context, disable draft edits, or
allow validation/tests/disposable requests. Questions should not create drafts;
read-only permission also blocks editing tools on the server.

Tool actions are expandable; checks remain tied to the exact source revision.
Conversations persist in private local checkpoints and support pause/resume.
Earlier read-only chats remain viewable as archives. Saved drafts remain in
**Drafts & checks**; the retired generation engine cannot resume old jobs.
See [Project AI and MCP](docs/mcp.md#project-ai-inside-the-dashboard).

**Endpoints → Edit source** still opens a handler in a fresh manual draft.
**Modify in Project AI**, failed-check repair, and mock-data preparation prefill
the conversation without making an AI request. Data previews open in **Mock data**
for manual editing, validation, copying/downloading, and separately confirmed
append-only insertion into existing tables. The AI cannot insert live rows.
Manual source/routes, checks, request testing, database setup, environment values,
approval, and activation remain available. See [mock data](docs/mock-data.md).

Generated APIs can now include a declarative `database/schema.json` plan. In
**Drafts & checks → Database setup**, review the SQL, validate/test on disposable
copies, then confirm creating new live tables with a pre-change backup. Existing
tables/records are preserved; code approval and activation remain separate.
See [`docs/database-setup.md`](docs/database-setup.md). Existing table changes use separate [reviewed migration plans](docs/maintenance.md).

For code expecting `JWT_SECRET` or another application variable, open
**Settings → Project environment**. Save a value or generate a secure JWT key,
rerun draft checks, and explicitly restart the published API. Values are stored
privately per project, never shown by settings APIs or sent to AI. See
[`docs/environment.md`](docs/environment.md) for security boundaries and limits.

Project AI handles questions, clarification, implementation and repair in one saved
conversation. Tool-capable Groq, OpenAI, compatible providers and local Ollama can
be configured; Anthropic remains preview-only. Edits and execution permissions
are server-enforced. Failed checks return feedback for repairs in the same draft;
there is no separate fixed two-attempt repair engine. Dependencies are checked
without automatic installation. To install a missing library manually through
the dashboard, use **Settings → Python packages** to resolve, review, and confirm
an installation into the shared virtual environment. See the
[package installation guide](docs/installation.md#install-missing-libraries-from-the-html-dashboard)
and [Project AI](docs/generation.md).

Remove a project through the running gateway (confirmation required):

```bash
uv run reqlica project delete "Book API"                # Unregister; keep files/data
uv run reqlica project delete <project-id> --delete-files # Also erase the project folder
```

Names and unique ID prefixes of six or more characters work. Add `--yes` to
confirm non-interactively. Deletion stops the worker and clears registry/runtime
state without restarting Reqlica; `project stop` alone does not remove a project.

## Change an API

Published mock routes can require per-project bearer credentials or API keys,
including expired credentials for client testing. Configure them through the
console API; see [mock authentication](docs/mock-authentication.md).
Per-route delays, synthetic failures (including the next N requests), and empty
responses are configurable through [gateway scenarios](docs/scenarios.md).
Rules also support [per-route and per-test-user rate limits](docs/rate-limits.md)
with HTTP 429 and `Retry-After` responses.
Inspect published request statuses, timings and correlated Python errors through
the [request history API and MCP](docs/inspection.md).
Save a SQLite baseline and restore it with a pre-reset backup, or rearm scenario
counters through the [reset APIs](docs/reset.md).
Copy active projects between installations using [validated ZIP export/import](docs/project-transfer.md).
Configure per-project browser origins, preflights and credentials through
[CORS settings](docs/cors.md).
External MCP clients can manage traffic policies, worker lifecycle, baselines/reset,
migration history and project archives/moves through the
[project operations tools](docs/mcp.md#project-operations). Live changes require
explicit human confirmation and current revisions; Project AI gets inspection only.

Create a draft rather than editing published versions:

```bash
uv run reqlica draft create <project-id> --json
uv run reqlica draft inspect <project-id> <draft-id>
```

For human checks, use a project name or unique ID prefix and a draft ID prefix
(six or more characters), without copying a revision hash:

```bash
uv run reqlica draft validate "Book API" <draft-prefix>
uv run reqlica draft test "Book API" <draft-prefix>
uv run reqlica draft url "Book API" <draft-prefix> /hello
```

The URL previews current draft code using disposable data. `draft call` also
resolves the revision automatically; explicit `--revision` is still supported.

Validation also saves revision-linked OpenAPI. Ask MCP's `get_api_schema` for
request/response formats, or `get_draft_urls` for Swagger and JSON links. See the
[API schema guide](docs/api-schemas.md); unknown/free-form contracts produce warnings
rather than guessed payloads.

Edit draft files/routes, validate and test the exact revision, then explicitly
approve and activate it. Draft checks use disposable database copies and do not
change live records through the SDK. See the [draft and version guide](docs/persistence-drafts-versions.md)
for commands, examples, legacy conversion, and recovery.

## Development

Run backend tests:

```bash
uv run python -m unittest discover -s tests
```

- [Development guide](docs/development.md): configuration, troubleshooting, project APIs, and code layout.
- [Drafts and versions](docs/persistence-drafts-versions.md): editing, checks, approval, persistence, and activation.
- [MCP tools](docs/mcp.md): connect an AI client and generate endpoint drafts.
- [API schemas](docs/api-schemas.md): generated contracts and draft/published Swagger docs.
- [Roadmap and design](docs/roadmap.md): planned features, MCP tools, and development order.

**Local trusted use only:** management APIs are unauthenticated. Keep servers on
loopback and only load trusted Python; separate workers are not a security sandbox.

Backend installation and the stateful bookstore demo: [installation](docs/installation.md).
Relocation, full workspace backup, migrations and rollback: [maintenance](docs/maintenance.md).
