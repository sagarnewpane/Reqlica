# Development

Follow [installation](installation.md) for prerequisites and setup.
Run commands from the repository root.

## Run and check

```bash
uv run reqlica start
uv run python -m unittest discover -s tests
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
```

Rebuild after frontend changes with `python3 scripts/build_frontend.py`.
Restart after Python/configuration changes. Backend integration tests need local
socket access. Frontend tests use Node's test runner; check the UI in a browser too.

To debug a server independently:

```bash
uv run python -m reqlica.console.app
uv run python -m reqlica.runtime.app
```

## Project management

```bash
uv run reqlica project create /absolute/new/books --name "Book API"
uv run reqlica project list
uv run reqlica project list --running --json
uv run reqlica project endpoints PROJECT_ID
uv run reqlica project start PROJECT_ID
uv run reqlica project stop PROJECT_ID
```

Creation works offline. Other commands above contact the gateway.
`--json` returns the API payload. Use the same `REQLICA_HOME` as the servers.

| Status    | Meaning                                         |
| --------- | ----------------------------------------------- |
| `idle`    | A request can start the worker.                 |
| `running` | Worker is serving the loaded version.           |
| `stopped` | Needs an explicit start; requests return 503.   |
| `error`   | Project files, registration, or startup failed. |

Stopped state lasts for the gateway session. Restarting the gateway clears it.

## Remove a project

```bash
# Stop and unregister; keep files.
uv run reqlica project delete "Book API"

# Permanently remove files, versions, drafts, and data after confirmation.
uv run reqlica project delete PROJECT_ID --delete-files
```

Deletion accepts exact names, full IDs, or unique ID prefixes of at least six
characters. `--yes` skips the prompt for deliberate scripted deletion.
The gateway must be running. It rechecks identity, paths, and locks before
removing files; it does not fall back to offline deletion. On failure, inspect
the reported state before retrying.

Management APIs are documented at `/docs` on both servers. Gateway project routes
use `/api/projects`: create/list at that prefix and start/stop/endpoints/delete
under `/{project_id}`. Console routes handle drafts, AI, data, and configuration.
See [drafts and versions](persistence-drafts-versions.md).

## Source layout

```text
backend/reqlica/
  cli.py        Startup and CLI
  console/      Dashboard and management API
  generation/   AI conversations and model/tool loop
  mcp/          Tools exposed to agents
  projects/     Project files, settings, data, and revisions
  runtime/      Gateway, workers, publication, database SDK
  validation/   Source checks and disposable execution
frontend/       Dashboard source
tests/          Python and frontend tests
docs/           Overview and guides
```

[Documentation](../README.md)
