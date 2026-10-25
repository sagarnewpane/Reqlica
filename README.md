# Reqlica

![Reqlica logo](logo.png)

Reqlica is a self-hosted mock backend for frontend developers and QA engineers.
Turn a description or API specification into a stateful Python API, check it on
copies of your data, then review and publish it. Published endpoints run Python
against persistent SQLite data without a model call for each request.

## Project documentation

- [Architecture overview](docs/architecture.md)
- [Technology details](docs/technology.md)
- [Limitations](docs/limitations.md) and [future improvements](docs/roadmap.md)
- [AI usage disclosure and Build with AI](docs/ai-usage.md)

Supporting setup, workflow, and API guides are in [docs/guides/](docs/guides/README.md).

## What it does

- **AI-assisted API authoring:** one saved conversation for specifications,
  endpoint creation, changes, tests, and repairs through MCP tools.
- **Review before publication:** editable drafts, revision-linked validation and
  behavior tests, then separate human approval and activation.
- **Stateful mock APIs:** independent project workers and persistent SQLite data,
  including generated signup/login flows when requested and properly configured.
- **Reliable sample-data preparation:** JSON, single-table, and related-record
  previews; a passed preview pauses for your review. Live insertion requires
  separate confirmation.
- **Client testing:** request tester and chains, OpenAPI/Swagger, mock access
  rules, delays, failures, rate limits, CORS, and request/error inspection.
- **Local project management:** environment variables, reviewed package installs,
  database setup/migrations, baselines, backups, and project transfer.

See [feature status](docs/guides/feature-status.md) for coverage and
[limitations](docs/limitations.md) before running generated code.

## Quick start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
macOS or Linux, and Node.js 20.9+ with npm to build the dashboard. From the repository root:

```bash
uv sync --locked
npm --prefix frontend ci
python3 scripts/build_frontend.py
uv run reqlica start
```

| Service                              | Default address           |
| ------------------------------------ | ------------------------- |
| Dashboard and console management API | http://127.0.0.1:3000     |
| Mock gateway                         | http://127.0.0.1:4000     |
| MCP, using Streamable HTTP           | http://127.0.0.1:3000/mcp |

Both servers expose interactive management API documentation at `/docs`.
Configure hosts, ports, and `REQLICA_HOME` through environment variables or the
checkout's root `.env`. Installed wheels include the dashboard and need no Node.js
server. See [installation](docs/guides/installation.md) for building a local wheel.

## Try a stateful API without an AI account

In another terminal:

```bash
uv run reqlica demo /absolute/new/bookstore
```

Use the printed project ID in place of `PROJECT_ID`:

```bash
curl http://127.0.0.1:4000/mock/PROJECT_ID/books
curl -X POST http://127.0.0.1:4000/mock/PROJECT_ID/orders \
  -H 'Content-Type: application/json' -d '{"book_id":1,"quantity":1}'
curl http://127.0.0.1:4000/mock/PROJECT_ID/books
```

Creating an order updates stock in the same SQLite transaction. Records survive
worker restarts. To create a starter project instead:

```bash
uv run reqlica project create /absolute/new/books --name "Book API"
uv run reqlica project list
```

The dashboard can also create projects. Paths refer to the machine running
Reqlica. Press `Ctrl+C` in the server terminal to stop servers and workers while
keeping saved projects and data.

## Build with Project AI

1. Open a project and configure a tool-capable model in Settings. Supported
   connections are Groq, OpenAI, OpenAI-compatible APIs, and local Ollama.
2. Describe the API or attach an OpenAPI specification in Project chat. Review
   the selected context and draft-edit/execution permissions before sending.
3. Inspect generated files, registered routes, and actual check results in Drafts.
   Configure required environment variables and review database plans separately.
4. Run validation and behavior tests for the final revision, review the source,
   then explicitly approve and activate it.
5. Use the request tester or a client against `/mock/PROJECT_ID/...`.

The same conversation handles clarifications and repairs. Data preparation uses
Sample data: validated previews stop for review and never insert live records
through Project AI. Test signup/login sequences in one behavior test run; separate
draft requests each discard their data changes.

External tool-capable agents can connect directly to the
[MCP server](docs/guides/mcp.md). Built-in Project AI cannot approve or publish code,
install packages, set secrets, or mutate live data. Some external MCP maintenance
tools support explicit, human-confirmed operations; their scope is documented.

## How AI is part of the core logic

Reqlica uses AI to transform requirements and specifications into structured tool
calls, Python handlers, tests, route definitions, and synthetic record batches.
The backend parses and validates those outputs, saves drafts, executes permitted
checks, and feeds diagnostics back into the repair loop. These outputs change
executable API behavior and validate datasets.

The exact programmatic consumption point is `step_run()` in
[`backend/reqlica/generation/agent.py`](backend/reqlica/generation/agent.py), which
consumes the model's `tool_calls`; `_execute_tool()` validates arguments and
executes allowed MCP actions. See [AI usage disclosure](docs/ai-usage.md) for the
full code path and development assistance statement.

## Development and documentation

```bash
uv run python -m unittest discover -s tests
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
```

See the [development guide](docs/guides/development.md) for configuration and
troubleshooting.

See the [documentation index](docs/README.md), [supporting guides](docs/guides/README.md),
and [frontend development guide](frontend/README.md).

**Trusted local use:** management APIs and MCP are unauthenticated. Keep them on
loopback. Generated Python runs with your OS and network permissions; separate
workers and disposable databases are not a security sandbox.
