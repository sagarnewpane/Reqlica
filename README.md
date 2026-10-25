# Reqlica

![Reqlica logo](logo.png)

Reqlica creates stateful mock APIs from a description or OpenAPI specification.
You review the generated Python, run checks, then approve and publish it.
Endpoints run with FastAPI and SQLite; serving requests does not call an AI model.

## Documentation

- [Installation](docs/guides/installation.md)
- [Architecture](docs/architecture.md)
- [Technology](docs/technology.md)
- [Limitations](docs/limitations.md) and [future work](docs/roadmap.md)
- [AI usage disclosure](docs/ai-usage.md)
- [Other guides](docs/guides/README.md)

## Quick start

Requires macOS or Linux, Python 3.12+, uv, Node.js 20.9+, and npm.
Run from the repository root:

```bash
uv sync --locked
npm --prefix frontend ci
python3 scripts/build_frontend.py
uv run reqlica start
```

Open the dashboard at http://127.0.0.1:3000. Mock requests use port `4000`.
MCP clients connect to `http://127.0.0.1:3000/mcp` using Streamable HTTP.
See the [installation guide](docs/guides/installation.md) for the full setup.

## What you can do

- Generate handlers, routes, tests, and sample data through Project AI or MCP.
- Edit drafts, run validation and tests, then approve and activate a revision.
- Test persistent APIs with a request tester, request chains, and Swagger.
- Simulate delays, failures, rate limits, authentication, and CORS.
- Manage data, environment variables, packages, backups, and project transfers.

## Try it without AI

With Reqlica running, open another terminal:

```bash
uv run reqlica demo /absolute/new/bookstore
```

Replace `PROJECT_ID` with the printed ID:

```bash
curl http://127.0.0.1:4000/mock/PROJECT_ID/books
curl -X POST http://127.0.0.1:4000/mock/PROJECT_ID/orders \
  -H 'Content-Type: application/json' -d '{"book_id":1,"quantity":1}'
```

Creating an order reduces stock. Data survives worker restarts.

## AI use

Use a model that supports tool calls. Project AI supports Groq, OpenAI,
OpenAI-compatible APIs, and Ollama. Reqlica exposes MCP tools so other agents, including OpenCode, can use them
with a tool-capable model and their own provider connection.

AI output becomes draft files, routes, and validated records. `step_run()` and
`_execute_tool()` in [agent.py](backend/reqlica/generation/agent.py) consume the
model's tool calls. See the [disclosure](docs/ai-usage.md) for details.

## Development

```bash
uv run python -m unittest discover -s tests
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend test
```

See [development](docs/guides/development.md) for more commands.

Reqlica is for trusted local use. Management APIs are unauthenticated, and
workers run Python with your OS permissions. Keep both servers on loopback.

## License

[MIT](LICENSE).
