# Local Development

## Requirements

Use Python 3.12 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and a POSIX system (macOS or Linux). Run commands from the repository root and use
the single root `.venv`:

```bash
uv sync --locked
uv run reqlica start
```

The current backend uses FastAPI, Pydantic, and SQLite. The frontend directory
contains a Next.js scaffold, not a working dashboard. The console currently
serves management APIs only; bundled static frontend installation is planned.

## Configuration

Configure the repository-root `.env` as needed:

```dotenv
CONSOLE_HOST=127.0.0.1
CONSOLE_PORT=3000
MOCK_HOST=127.0.0.1
MOCK_PORT=4000
```

These are the defaults. Keep the two ports different. Exported environment
variables take precedence over `.env`. Set `REQLICA_HOME` to override the default
registry location, `~/.reqlica/projects/`. Use the same value for project creation,
both servers, and local draft approval. Projects themselves can live in any
directory Reqlica can access; keep registered directories in place because
moving/importing projects is not implemented.

Keep servers bound to loopback. Management APIs are unauthenticated, and Python
endpoint code runs with the user's permissions, not inside a security sandbox.
Browser CORS configuration is not implemented.

## Startup and Debugging

`reqlica start` launches the console and shared mock gateway. Each project worker
starts on demand or through an explicit project start command. `Ctrl+C` shuts down
the servers and workers without deleting project files or data. If either server
exits unexpectedly, the CLI stops the other server and exits with an error.

Restart after backend or configuration changes; automatic reload is not enabled.
For debugging, each server can run independently:

```bash
uv run python -m reqlica.console.app
uv run python -m reqlica.runtime.app
```

Check the default addresses:

```bash
curl http://127.0.0.1:3000/api/health
curl http://127.0.0.1:4000/mock/demo/hello
```

The demo returns `{"message":"Hello from Reqlica","project_id":"demo"}`.
Each main server exposes interactive API documentation at `/docs`; project
workers do not expose documentation routes.

If `reqlica` is not found, use `uv run reqlica` or activate `.venv`:

```bash
source .venv/bin/activate
reqlica start
```

An optional editable CLI installation is available for local development:

```bash
uv tool install --editable .
uv tool update-shell
```

Open a new terminal after updating the shell. This uses the checkout and its
repository-root `.env`; it is not a published package installation.

## Project Management

```bash
uv run reqlica project create /path/to/books --name "Book API"
uv run reqlica project list
uv run reqlica project list --running --json
uv run reqlica project endpoints <project-id>
uv run reqlica project start <project-id>
uv run reqlica project stop <project-id>
```

Creation works without running servers. The other commands contact the gateway,
not the console, and do not import project handlers into the CLI. `--json` returns
the unchanged API payload. Errors go to stderr and produce a nonzero exit status.
HTTP clients bypass environment proxies and use a 30-second timeout for these
project commands. CLI connections map wildcard bind hosts to loopback and bracket
IPv6 addresses.

| Status | Meaning |
|---|---|
| `idle` | No worker; a mock request can start one. |
| `running` | A worker is serving its loaded code and routes. |
| `stopped` | Explicitly stopped for this gateway session; mock requests return `503` until an explicit start. |
| `error` | Registry, project files, or worker state could not be read or started. |

Stopping is idempotent and keeps files/data. Restarting the gateway resets its
session-only stopped state. Discovery does not start workers and reports broken
registry entries as error projects rather than hiding them.

The gateway's management routes are:

| Method | Route | Result |
|---|---|---|
| `GET` | `/api/projects` | All registered projects in a `projects` array. |
| `GET` | `/api/projects?running=true` | Running projects only. |
| `GET` | `/api/projects/{id}/endpoints` | Project status and endpoints. |
| `POST` | `/api/projects/{id}/start` | Start or reuse the worker. |
| `POST` | `/api/projects/{id}/stop` | Stop and disable lazy restart. |
| `POST` | `/api/projects/{id}/convert` | Convert an idle/stopped legacy project. |
| `POST` | `/api/projects/{id}/activate` | Publish an approved `{draft_id, expected_revision}`. |

Project payloads include `id`, `name`, `directory`, `status`, `pid`, `error`, `url`,
`endpoints`, and `active_version`. Endpoints include `method`, `path`, `handler`,
and a full `url`. Unknown IDs return `404`, malformed stored files normally return
`422`, and worker failures return `502`. Conversion/activation conflicts return
`409`; activation temporarily rejects new project requests with `503`.

An unexpected management-route `404` can mean the gateway is running older code;
restart it. See the [draft and version guide](persistence-drafts-versions.md) for
editing, approval, activation, database access, and recovery.

## Code Layout

```text
backend/reqlica/
  cli.py        # Startup and CLI commands
  console/      # Management API
  projects/     # Registry, drafts, and versions
  runtime/      # Gateway, workers, activation, and database SDK
  validation/   # Static checks and disposable execution
  generation/   # Planned AI integration
  mcp/          # Planned MCP integration
frontend/       # Dashboard scaffold
tests/          # Backend tests
docs/           # Usage and design references
```

Run the backend suite with:

```bash
uv run python -m unittest discover -s tests
```
