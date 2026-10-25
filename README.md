# Reqlica

<img src="logo.png" alt="Reqlica logo" width="240">

Reqlica creates stateful mock APIs from a description or OpenAPI specification.
You review the generated Python, run checks, then approve and publish it.
Endpoints run with FastAPI and SQLite; serving requests does not call an AI model.

Use the web interface or connect an MCP-capable agent of your choice, such as
OpenCode, with its own model and credentials.

![Reqlica project chat dashboard](docs/images/project-chat.png)

## Features

- **AI API generation:** turn a description or OpenAPI specification into Python
  endpoints, route definitions, and tests.
- **Persistent data:** keep SQLite records across requests, worker restarts,
  and code updates.
- **Review before publishing:** edit drafts, run validation and behavior tests,
  then approve and activate the checked version.
- **Sample data:** generate and validate JSON fixtures or related database
  records before confirming insertion.
- **Request testing:** try endpoints with the built-in tester, chain requests,
  and inspect OpenAPI contracts in Swagger.
- **Traffic controls:** configure delays, failures, rate limits, mock credentials,
  and browser access through CORS.
- **Logs and recovery:** inspect requests and errors, restore data baselines,
  and back up or transfer projects.
- **Runtime management:** start and stop APIs, configure environment variables,
  review package installs, and apply database changes.

## Quick start

Requires macOS or Linux, Git, Python 3.12+, and
[uv](https://docs.astral.sh/uv/getting-started/installation/).
The built dashboard is included; Node.js is only needed for frontend development.

```bash
git clone https://github.com/sagarnewpane/Reqlica.git
cd Reqlica
uv sync --locked
cp .env.example .env
uv run reqlica start
```

Open http://127.0.0.1:3000. `uv` creates and uses `.venv` automatically.
[.env.example](.env.example) documents the defaults; edit `.env` to change ports
or the app-state directory. `Ctrl+C` stops the servers without deleting data.
See [installation](docs/guides/installation.md) for the full setup.

## Use Reqlica

**Web interface:** create a project, configure a tool-capable model in Settings,
and describe your API in Project chat or attach an OpenAPI specification.
Review the draft, run validation and tests, then approve and activate it.

**External agent:** connect your MCP-capable agent to:

```text
Transport: Streamable HTTP
URL: http://127.0.0.1:3000/mcp
```

The agent uses its own model/provider connection. Ask it to inspect your project
and create or update a draft. Review, approve, and activate through the web
interface or CLI. See [MCP tools](docs/guides/mcp.md) for details.

Published endpoints are available at
`http://127.0.0.1:4000/mock/PROJECT_ID/YOUR_PATH`. Test them with the built-in
request tester or your frontend.

## AI use

Use a model that supports tool calls. Project AI supports Groq, OpenAI,
OpenAI-compatible APIs, and Ollama.

AI output becomes draft files, routes, and validated records. `step_run()` and
`_execute_tool()` in [agent.py](backend/reqlica/generation/agent.py) consume the
model's tool calls. See the [disclosure](docs/ai-usage.md) for details.

## Documentation

- [Installation](docs/guides/installation.md)
- [Architecture](docs/architecture.md)
- [Technology](docs/technology.md)
- [Limitations](docs/limitations.md) and [future work](docs/roadmap.md)
- [AI usage disclosure](docs/ai-usage.md)
- [Other guides](docs/guides/README.md)

## Development

See [development](docs/guides/development.md) for frontend builds, server integration,
and checks. Users can run the included dashboard without rebuilding it.

Reqlica is for trusted local use. Management APIs are unauthenticated, and
workers run Python with your OS permissions. Keep both servers on loopback.
