# Technology details

Reqlica uses a Python backend and a dashboard built with Next.js and React. The
built dashboard is bundled with the backend and served by Reqlica. Project code
and metadata are stored on disk; each project's API data is stored in SQLite.
AI providers are used during authoring. Published endpoints execute Python
without model calls.

## Backend

| Technology   | Responsibility                                                                                           |
| ------------ | -------------------------------------------------------------------------------------------------------- |
| Python 3.12+ | Runs the CLI, management servers, project workers, and generated API handlers.                           |
| FastAPI      | Provides the console API, mock gateway, and each project's API routes. Also generates OpenAPI contracts. |
| Uvicorn      | Serves the FastAPI applications.                                                                         |
| Pydantic     | Validates request bodies, project metadata, route definitions, and model settings.                       |
| SQLite       | Stores each project's API records in its own database file. No database server is needed.                |
| HTTPX        | Forwards mock requests to workers and handles HTTP transport for AI and MCP calls.                       |

Workers run in separate processes and load standard Python handlers registered
with FastAPI. Draft checks use subprocesses and disposable database copies.
All projects share the installed Python environment; dependencies are not
isolated per project.

## Dashboard

| Technology                     | Responsibility                                                 |
| ------------------------------ | -------------------------------------------------------------- |
| Next.js 16, Pages Router       | Builds dashboard pages as static HTML, JavaScript, and CSS.    |
| React 19                       | Renders the dashboard and manages interactive UI state.        |
| TypeScript 5                   | Provides static type checking for frontend code.               |
| CSS Modules and Tailwind CSS 4 | Style feature panels, shared controls, and themes.             |
| shadcn/ui and Radix UI         | Supply controls such as dialogs, tabs, menus, and the sidebar. |
| Lucide                         | Supplies interface icons.                                      |
| react-markdown                 | Displays Markdown in chat.                                     |

Node.js 20.9+ and npm provide the frontend build tools. `next build` exports static
assets to `frontend/out/`; `scripts/build_frontend.py` stages them inside the
Python package. FastAPI serves the UI and handles dashboard API requests through
`console/dashboard_api.py`. No Node.js server is required at runtime.

## Model integration and MCP

The OpenAI Python SDK sends chat-completion requests with tool definitions to
Groq, OpenAI, OpenAI-compatible providers, and local Ollama. The chosen model
must support tool calls. The current backend conversation API does not accept
Anthropic profiles.

The MCP Python SDK exposes project tools over Streamable HTTP. Project AI uses
these tools to read context, write draft files, register routes, and run allowed
checks. External MCP clients can connect to the same server.

Reqlica implements the conversation loop in
[`generation/agent.py`](../backend/reqlica/generation/agent.py). It saves progress
between model requests and tool execution, enforces permissions and budgets,
and returns tool results to the model. Approval and activation are separate
user actions.

## API contracts and validation

- **OpenAPI** describes request and response formats and powers Swagger docs.
- **PyYAML** reads YAML specifications; JSON specifications use Python's JSON tools.
- **jsonschema** checks supported contract rules and mock-data structures.
- **Python AST and Pyflakes** catch syntax and undefined-name issues in draft source.
- **unittest and FastAPI TestClient** run behavior checks on disposable code and
  database copies.

Contract validation covers supported OpenAPI constructs. Static checks and tests
provide evidence for a draft revision; they do not guarantee complete behavior
coverage or provide a security sandbox.

## Persistence

Source, route manifests, drafts, published versions, and saved conversations live
on disk. SQLite stores live API records. Code history uses Reqlica's own file
snapshots; it does not require a Git repository for each mock project.

## Build and quality tools

The backend uses **uv** with `uv.lock` for dependency installation and
**Hatchling** to build its Python package. The frontend uses **npm** with
`package-lock.json`. **ESLint**, **TypeScript**, and **Prettier** check frontend
code; Node's test runner covers frontend helpers and components. Python tests cover
the dashboard's server adapters and static asset serving.

Dependency requirements are declared in [pyproject.toml](../pyproject.toml) and
[frontend/package.json](../frontend/package.json). Lockfiles record resolved
versions for repeatable installation. See [Architecture](architecture.md) for
component boundaries and execution flows.

[Documentation index](README.md) · [Project overview](../README.md)
