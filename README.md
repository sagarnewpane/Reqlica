# Reqlica

<img width="640" height="320" alt="Reqlica_Logo" src="https://github.com/user-attachments/assets/f2fd5444-1ecd-4ffc-bc58-2c236579745f" />

Reqlica is a planned open-source, self-hosted tool for building mock backends from feature descriptions, API documentation, or OpenAPI specs. It helps frontend developers and QA engineers work before a backend is ready.

The AI writes Python endpoint functions, including input validation and business logic. Users review the result, make changes, and start the API from a browser dashboard. Test data stays saved between requests and restarts.

This document is a project reference during development. Most features below are planned; the local development section describes the current setup.

## Planned features

- [x] **Terminal startup:** run `reqlica start` to launch the console and mock server.
- [x] **CLI project management:** create, list, inspect endpoints, start, and stop separate mock APIs.
- [x] **Custom project locations:** save projects in a directory of the user's choice.
- [ ] **AI generation:** accept prompts, Markdown, plain text, and OpenAPI specs.
- [ ] **Python endpoints:** generate request/response models and functions with custom processing logic.
- [ ] **Review and editing:** inspect endpoints, fields, relationships, assumptions, and code before publishing changes.
- [ ] **Manual changes:** edit fields or Python code and add or remove endpoints through the dashboard.
- [ ] **REST support:** CRUD operations, filtering, pagination, and custom actions.
- [ ] **Test data:** generate records with chosen counts, ranges, enums, and relationships.
- [ ] **Business logic:** field validation, uniqueness, reference checks, state changes, and database transactions.
- [ ] **Test authentication:** bearer tokens and API keys, including invalid and expired credentials.
- [ ] **Failure scenarios:** errors, delays, empty results, and failure of the next N requests.
- [ ] **Rate limits:** limits per route and test user, with `429` responses.
- [ ] **Inspection:** browse endpoints and records, inspect requests and responses, and view runtime errors.
- [ ] **Persistence and reset:** keep saved data and restore a project's starting state.
- [ ] **Drafts and versions:** prepare and test changes before replacing a running version.
- [x] **Separate project runtimes:** run endpoint code outside the console and gateway processes.
- [ ] **Export and import:** share complete project bundles and export OpenAPI specs.
- [ ] **Local deployment:** bundle the dashboard with the Python package and support open-weight AI models.

## How it works

The entry command is:

```bash
reqlica start
```

It starts the console and the shared mock server. Opening a browser dashboard is planned; the current console exposes only its API.

From the console, a user creates a project or opens an existing one. Each project contains its own endpoints, data, authentication settings, and test scenarios. Projects can run at the same time and can be started or stopped independently.

For example, a user could describe this API:

> Create a bookstore API. Books have a title, price, and stock count. Customers can place orders. Reject an order if there is not enough stock, and reduce stock when an order succeeds.

Reqlica turns that description into endpoint code and data models. After review and testing, the project exposes a URL that a frontend can use.

Stopping a project keeps its files and data. Stopping Reqlica stops the console, mock server, and running project processes.

## From a description to a working endpoint

1. **Read the requirements.** The AI reads the description or imported document alongside the existing project. Missing details appear as questions or assumptions.
2. **Plan the API.** It identifies the endpoints, methods, inputs, outputs, data models, and business logic.
3. **Write the code.** It generates Python functions and supporting models, tests, and proposed database changes.
4. **Save a draft.** MCP tools write these changes into an unpublished project version.
5. **Check and test.** Reqlica checks the code and runs it with a separate test database. Errors return to the AI so it can make corrections.
6. **Review the changes.** The dashboard shows the generated endpoints, assumptions, code changes, and test results. The user can edit them before approving the version.
7. **Publish and run.** Reqlica activates the approved version and starts or updates the project's process. Its endpoints are then available through the mock server.

```text
Description or API document
            ↓
    AI plans and writes Python
            ↓
    MCP tools save a draft
            ↓
      Checks and tests
            ↓
    User reviews and approves
            ↓
     Project version goes live
```

OpenAPI files provide routes, schemas, and documented responses. Business rules missing from the specification appear as proposed behavior for review.

Once an endpoint is running, requests execute its Python function. They do not need another AI call.

## Servers and project URLs

Reqlica has two local servers: one for the console and one for all mock API traffic. The ports below are proposed defaults.

```text
reqlica start
    │
    ├── Console: localhost:3000
    │       └── Dashboard and management API
    │
    ├── Mock server: localhost:4000
    │       ├── /mock/books-id/* → Books project
    │       └── /mock/cafe-id/*  → Cafe project
    │
    └── Process supervisor
            └── Starts and stops project workers
```

Each generated project runs in a separate worker process. The shared mock server acts as a gateway: it reads the project ID from the URL and forwards the request to that project's worker. The console and gateway use Reqlica's own code; the workers load the generated endpoint code.

Two projects can have the same endpoint without a conflict:

```text
POST localhost:4000/mock/books-id/login
POST localhost:4000/mock/cafe-id/login
```

Project IDs stay the same after a rename or restart. Each project has its own database, tokens, rate counters, failure settings, and logs. The frontend uses the project's URL as its API base URL. Browser access to mock APIs includes configurable CORS settings.

## Generated code

The AI writes the endpoint's logic. Reqlica provides shared helpers for database access, transactions, authentication, responses, and logging.

For a book order, the generated function validates the quantity, finds the book, checks stock, creates the order, and reduces the stock count. The database changes happen together: a failed order leaves stock unchanged. Stock updates also need to handle simultaneous orders without selling the same stock twice.

Endpoint code is saved in a draft. When that version goes live, the project worker loads its functions and registers their routes. Changing the draft does not change the running API until the new version is activated.

## MCP tools

The AI uses named MCP tools to work on a project. These tools call the same project operations used by the dashboard.

| Tool | Purpose |
|---|---|
| `get_project_context` | Read existing endpoints, schemas, source files, and available Reqlica helpers. |
| `create_draft` | Create an editable project version. |
| `upsert_endpoint` | Add or replace an endpoint's Python function and route details. |
| `write_draft_file` | Save supporting models, database migrations, seed scripts, and tests. |
| `remove_endpoint` | Remove an endpoint from the draft. |
| `validate_draft` | Check code, imports, and route conflicts. |
| `test_draft` | Run tests with separate test data. |
| `call_draft_endpoint` | Send a test request and return its response and logs. |
| `get_draft_changes` | Show changes and test results for review. |
| `activate_draft` | Publish the tested version approved by the user. |
| `read_project_logs` | Read request logs and runtime errors. |

Optional project management tools:

| Tool | Purpose |
|---|---|
| `list_projects` | List registered projects and their locations. |
| `create_project` | Create a project in the chosen directory. |
| `open_project` | Open an existing project for inspection and editing. |
| `get_project_status` | Read a project's runtime status and mock API URL. |
| `start_project` | Start a project's worker and expose its active API version. |
| `stop_project` | Stop a project's worker while keeping its files and data. |

Approval belongs to the exact version reviewed. Further edits require fresh checks and approval. The AI cannot approve its own changes.

## Editing and drafts

A draft is a folder containing an unpublished version of a project. Both the AI and the user can edit it. The active version keeps running while those changes are prepared.

The dashboard supports changes to endpoint paths, methods, fields, validation, and Python code. Removing an endpoint removes its route when published; its stored records remain unless they are explicitly deleted.

Some field edits also affect code and data. Renaming `stock`, for example, may require changes to the endpoint functions and a database migration. Those related changes appear in the review.

Tests use a separate database so draft requests do not change the active project's records.

## Project storage

Projects can be saved in any directory Reqlica has permission to access. A small central registry records each project's ID and location, so the console can find projects stored in different folders.

A proposed project layout is:

```text
<project-directory>/
├── project.json             # Project ID, settings, and active version
├── drafts/
│   └── draft-001/
│       ├── manifest.json    # Routes and handler locations
│       ├── endpoints/
│       ├── models.py
│       ├── migrations/
│       ├── seeds/
│       └── tests/
├── versions/
│   └── version-001/         # Published code
├── data.sqlite              # Current project data
├── baseline.sqlite          # Starting data used by reset
└── logs/
```

Moving a project changes its registered location, not its ID. Reset restores its baseline data and test scenario state.

A complete export includes source code, models, settings, and database migrations, with an optional starting dataset. An OpenAPI export describes the API contract but does not contain the Python business logic.

## Codebase structure

The application code and user project files are stored separately. The proposed application layout is:

```text
reqlica/
├── backend/
│   └── reqlica/
│       ├── cli.py           # Terminal commands and startup
│       ├── console/         # Dashboard API and built frontend files
│       ├── projects/        # Project storage, drafts, and versions
│       ├── generation/      # AI integration and generation loop
│       ├── mcp/             # MCP tools and client integration
│       ├── runtime/         # Gateway, workers, and process supervisor
│       │   └── sdk/         # Helpers used by generated endpoints
│       └── validation/      # Code checks and test execution
├── frontend/                # Dashboard source
├── tests/                   # Tests for Reqlica itself
├── docs/
└── pyproject.toml
```

Python is the backend language. FastAPI, Pydantic, and SQLite are the proposed starting stack.

The frontend can use any framework that builds static HTML, CSS, and JavaScript. Those files ship with the Python package and are served by the console server. Users will not need Node.js to run the dashboard.

## Local development

Use a single Python virtual environment at the repository root: `.venv/`.
This setup requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run the following commands from the repository root.
Install the locked dependencies (this creates `.venv/` if needed):

```bash
uv sync --locked
```

Configure both servers in the repository-root `.env` file:

```dotenv
CONSOLE_HOST=127.0.0.1
CONSOLE_PORT=3000
MOCK_HOST=127.0.0.1
MOCK_PORT=4000
```

These are the defaults when settings are omitted. Change `CONSOLE_PORT` or
`MOCK_PORT` to use custom ports, keeping them different. Already-exported
environment variables take precedence over `.env` values.

Start both servers from one terminal:

```bash
uv run reqlica start
```

Alternatively, activate the environment and use the installed command directly:

```bash
source .venv/bin/activate
reqlica start
```

`uv sync` installs the command inside `.venv`, not globally. If `reqlica start`
reports `command not found`, activate `.venv` in that terminal or use
`uv run reqlica start`. Activation applies only to the current terminal session.

To make the command available without activating `.venv`, optionally install
the checkout as an editable CLI tool:

```bash
uv tool install --editable .
uv tool update-shell
```

Open a new terminal after updating the shell, then run `reqlica start` directly.
The editable installation uses this checkout's code and repository-root `.env`.
This is a local development installation, not a published package release.

Press `Ctrl+C` to stop both servers. If either server exits unexpectedly, the CLI
stops the other server and exits with an error. Restart `reqlica start` after
changing settings or backend code; automatic reload is not enabled.

For debugging, either server can still be run independently:

```bash
uv run python -m reqlica.console.app
uv run python -m reqlica.runtime.app
```

Check both connections using the default ports:

```bash
curl http://127.0.0.1:3000/api/health
curl http://127.0.0.1:4000/mock/demo/hello
```

The mock endpoint returns:

```json
{"message":"Hello from Reqlica","project_id":"demo"}
```

The demo endpoint remains available for connection checks. Real project endpoints
also run through the mock gateway, as described below. Browser CORS configuration
is not implemented yet. Each main server exposes interactive API documentation
at `/docs`; project workers do not expose documentation routes.
Use your configured ports in these URLs if they differ from the defaults.

### Create a mock endpoint

Create a project in a new or empty directory:

```bash
uv run reqlica project create /path/to/books --name "Book API"
```

The command prints the project's stable ID and its `/hello` URL. It creates:

```text
books/
├── project.json          # Stable ID and display name
├── manifest.json         # HTTP methods, paths, and Python handlers
└── endpoints/
    └── hello.py          # A working GET /hello handler
```

The registry stores project locations in `~/.reqlica/projects/`. Set `REQLICA_HOME`
in the environment or repository-root `.env` to use a different registry root.
Use the same setting for project creation and server startup. Moving or importing
existing projects is not supported yet; keep registered directories in place.

With `reqlica start` running, call the URL printed by the creation command:

```bash
curl http://127.0.0.1:4000/mock/<project-id>/hello
```

The first request starts that project's worker. Further requests reuse it.
Creating another project while Reqlica is running does not require a restart.
Stopping Reqlica stops its workers and keeps project files and registry entries.

### Manage mock projects

With `uv run reqlica start` running in another terminal, use the printed
32-character lowercase hexadecimal project ID in these commands:

```bash
uv run reqlica project list
uv run reqlica project list --running
uv run reqlica project endpoints <project-id>
uv run reqlica project start <project-id>
uv run reqlica project stop <project-id>
```

These commands contact the running mock gateway, not the console, and do not load
project Python code into the CLI. They show project IDs, statuses, and full
endpoint URLs. `list` includes faulty registry entries as `error` projects rather
than hiding them; `--running` asks the gateway to return only running projects.
Unlike `project create`, management commands require a running gateway. If it is
unreachable, run `reqlica start` first and check its host and port settings.

Each management command supports `--json` for the unchanged API payload, without
human-readable summaries. For example:

```bash
uv run reqlica project list --running --json
uv run reqlica project endpoints <project-id> --json
uv run reqlica project start <project-id> --json
uv run reqlica project stop <project-id> --json
```

Errors go to stderr and return a nonzero exit status, including in JSON mode.
The CLI loads `MOCK_HOST` and `MOCK_PORT` from the repository-root `.env`, with
exported environment variables taking precedence. Requests bypass environment
proxy settings and have a 30-second timeout. Wildcard bind addresses are mapped
to loopback for CLI connections (`0.0.0.0` to `127.0.0.1`, `::` to `::1`);
IPv6 URL hosts use brackets, for example `http://[::1]:4000`.

| Status | Meaning |
|---|---|
| `idle` | No worker is running; calling a mock endpoint can lazily start it. |
| `running` | A worker is running with its loaded endpoints. |
| `stopped` | Explicitly stopped in this gateway session; mock requests return `503` until an explicit `project start`. |
| `error` | Project files or worker state could not be read or started; inspect the error detail. |

`project stop` is idempotent and keeps the project's files and registry entry.
It disables lazy restart for that project until `project start` is called.
Restarting the gateway resets the session's stopped state, allowing lazy startup
again. Listing projects or endpoints does not start idle or stopped workers.

The gateway's interactive API documentation is at
`http://127.0.0.1:4000/docs` (use your configured host and port). Project
management is available through these routes:

| Method | Route | Result |
|---|---|---|
| `GET` | `/api/projects` | All registered projects in a `projects` array. |
| `GET` | `/api/projects?running=true` | Only running projects. |
| `GET` | `/api/projects/{id}/endpoints` | A single project's status and endpoints. |
| `POST` | `/api/projects/{id}/start` | Start the worker and return the project. |
| `POST` | `/api/projects/{id}/stop` | Stop the worker and return the project. |

Each project object includes `id`, `name`, `directory`, `status`, `pid`, `error`,
`url`, and `endpoints`. Each endpoint includes `method`, `path`, `handler`, and
its full `url`. Unknown IDs return `404`, malformed stored project files return
`422`, and worker failures return `502`. A gateway already running older code
must be restarted to load these management routes; an unexpected `404 Not Found`
from a management command can mean the server needs that restart.

This adds CLI and API management only, not a GUI dashboard. Keep the gateway
bound to loopback: management routes are unauthenticated and can start local
Python code. Do not expose them to untrusted networks.

### Edit mock endpoints

To define your own endpoint, add a function in `endpoints/hello.py` or another
project-relative Python file. Handlers use standard FastAPI signatures, including
path/query parameters, Pydantic request models, `Request`, and `Response`:

```python
from pydantic import BaseModel

class Greeting(BaseModel):
    name: str

def greet(body: Greeting) -> dict[str, str]:
    return {"message": f"Hello, {body.name}"}
```

Add the route to the `endpoints` array in `manifest.json`, alongside `/hello`:

```json
{
  "method": "POST",
  "path": "/greet",
  "handler": "endpoints/hello.py:greet"
}
```

After editing a loaded project's manifest or Python code, stop and start its
worker to load the changes, then call the endpoint:

```bash
uv run reqlica project stop <project-id>
uv run reqlica project start <project-id>
curl -X POST http://127.0.0.1:4000/mock/<project-id>/greet \
  -H 'Content-Type: application/json' \
  -d '{"name":"Sam"}'
```

While a worker is running, its endpoint list reflects the loaded snapshot, not
subsequent disk edits. `project start` on an already-running worker does not
reload it; use stop/start. Restarting Reqlica also reloads project code when its
worker next starts, but is only necessary for gateway/backend code changes.

Supported methods are `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`, `HEAD`,
and `TRACE`. Route paths are relative to the project's `/mock/<project-id>` base.
Unknown project IDs return `404`; worker startup and connection failures return
`502` with an error description. Explicitly stopped projects return `503` until
started again. Drafts, version activation, and dashboard endpoint editing remain
planned.

Only load trusted Python code. A separate worker process is not a security sandbox:
handlers run with the same user permissions as Reqlica. Keep the servers bound to
loopback; authentication for exposing them to other machines is not implemented.

Run the backend tests:

```bash
uv run python -m unittest discover -s tests
```

## Development order

1. CLI startup, console, project storage, and the shared mock server.
2. A manually written endpoint running in its own project worker.
3. Persistent data, drafts, testing, and version activation.
4. AI generation through MCP, starting with one simple endpoint.
5. Stateful workflows, authentication, failures, rate limits, and inspection.
6. Reset, export/import, installation instructions, and a working demo.

## Still to decide

- How generated Python is isolated and which files, networks, and dependencies it can access. A separate process alone does not provide a security sandbox.
- The Python handler format and database helpers available to generated code.
- How database changes are applied and recovered if an update fails. Returning to older code may also require restoring a compatible database.
- The frontend framework, AI model/provider, and MCP transport.
- The supported OpenAPI features and project export format.
- The license and whether installation requires a container runtime.

A later release may run approved scenarios against a real test backend and provide a CI runner.
