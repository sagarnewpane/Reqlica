# Technology

| Area                       | Stack                                                          |
| -------------------------- | -------------------------------------------------------------- |
| Backend and generated APIs | Python 3.12+, FastAPI, Uvicorn, Pydantic                       |
| Data                       | Per-project SQLite databases; JSON metadata and file snapshots |
| Dashboard                  | Next.js 16 Pages Router, React 19, TypeScript 5                |
| Styling and controls       | CSS Modules, Tailwind CSS 4, shadcn/ui, Radix UI, Lucide       |
| AI transport               | OpenAI Python SDK; Groq, OpenAI, compatible APIs, and Ollama   |
| Agent tools                | MCP Python SDK over Streamable HTTP                            |
| Validation                 | Python AST, Pyflakes, jsonschema, unittest, FastAPI TestClient |
| Specifications             | OpenAPI, JSON, PyYAML                                          |
| Build and checks           | uv, Hatchling, npm, ESLint, Prettier, Node's test runner       |

The frontend build exports static assets into `frontend/out/`.
`scripts/build_frontend.py` copies them into the Python package. Node.js 20.9+
is needed to build the dashboard, but not to run an installed wheel.

AI authoring requires tool calls. External clients connect through MCP; built-in
Project AI manages the model/tool loop itself. Published endpoints use FastAPI
and SQLite without further inference. All projects share one Python environment.

[pyproject.toml](../pyproject.toml) and
[frontend/package.json](../frontend/package.json) declare dependencies.
`uv.lock` and `package-lock.json` pin resolved versions.

[Architecture](architecture.md) · [Documentation](README.md)
