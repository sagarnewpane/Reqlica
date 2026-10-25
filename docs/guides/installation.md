# Installation

## Requirements

- macOS or Linux.
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Git to clone the repository.

SQLite is included with Python; no database server or container is needed.
The built dashboard is included in the repository and Python wheel. Users do
not need Node.js, npm, or a frontend build.

Check your tools:

```bash
python3 --version
uv --version
```

## 1. Get the source

```bash
git clone https://github.com/sagarnewpane/Reqlica.git
cd Reqlica
```

Run the remaining checkout commands from this directory.

## 2. Install Python dependencies

```bash
uv sync --locked
```

`uv sync` creates the root `.venv`. The console serves the included dashboard
from `backend/reqlica/static/`; no separate frontend server or build is needed.

## 3. Configure the servers

Copy the provided [environment example](../../.env.example) to `.env` in the
repository root, then edit it as needed:

```bash
cp .env.example .env
```

The defaults are:

```dotenv
CONSOLE_HOST=127.0.0.1
CONSOLE_PORT=3000
MOCK_HOST=127.0.0.1
MOCK_PORT=4000
# Optional: registry, app settings, and provider keys
# REQLICA_HOME=/absolute/path/to/reqlica-home
```

Environment variables override `.env`. Keep the ports different and use the same
`REQLICA_HOME` for CLI commands and both servers. Project directories can live
elsewhere. Do not put project secrets in this file; use **Runtime → Environment**.

Keep both hosts on loopback. Management APIs are unauthenticated, and generated
Python runs with your OS permissions.

## 4. Start Reqlica

```bash
uv run reqlica start
```

| Service              | Default URL                |
| -------------------- | -------------------------- |
| Dashboard            | http://127.0.0.1:3000      |
| Console API docs     | http://127.0.0.1:3000/docs |
| Mock gateway docs    | http://127.0.0.1:4000/docs |
| MCP, Streamable HTTP | http://127.0.0.1:3000/mcp  |

Keep the terminal open. `Ctrl+C` stops the servers and workers without deleting
projects or data. Restart after Python or server-configuration changes.

Check the console from another terminal:

```bash
curl --fail http://127.0.0.1:3000/api/health
```

## 5. Create a project

Use **Create project** in the web interface, or run this from another terminal:

```bash
uv run reqlica project create /absolute/new/books --name "Book API"
```

The starter includes a `/hello` handler and test. Paths refer to the server machine.

## 6. Configure AI

In dashboard **Settings**, choose Groq, OpenAI, an OpenAI-compatible provider,
or Ollama. Select a model with tool-call support and save its key when required.
Local Ollama needs no key; install and run your chosen model separately.

For an external agent, use an MCP-capable client and configure this server:

```text
Transport: Streamable HTTP
URL: http://127.0.0.1:3000/mcp
```

The client handles its own model connection and credentials. A text-only model
cannot run the authoring tools. See [MCP](mcp.md) for the tool workflow.

In Project chat, describe an API or attach a specification. Review the generated
draft, run validation and tests, then approve and activate it separately.

## Build and install a local wheel

The included dashboard is packaged automatically:

```bash
uv build
uv tool install ./dist/reqlica-0.1.0-py3-none-any.whl
reqlica --help
reqlica start
```

This installs a local wheel, not a published PyPI release. The wheel includes
the dashboard; its host needs Python 3.12+ but no Node.js server.
Use explicit environment variables for installed deployments rather than relying
on the checkout's `.env`. For later builds, use the wheel's actual filename and
reinstall with `uv tool install --force PATH_TO_WHEEL`.

## Install missing libraries from the dashboard

Open **Runtime → Python packages**:

1. Inspect missing imports and verify the distribution name, such as `jwt` → `PyJWT`.
2. Choose **Resolve & preview** and review every proposed package/version.
3. Choose **Install reviewed packages…** and confirm the displayed environment.
4. Restart affected APIs and rerun validation and tests.

The console needs `uv` and a writable virtual environment shared with the gateway.
Only reviewed, hash-pinned PyPI wheels are accepted. Installation affects every
project and invalidates draft checks. Existing versions are constrained; upgrades,
removals, source builds, and arbitrary URLs are not supported here.

After an uncertain result, inspect the saved job before retrying. Installs do not
update `pyproject.toml` or `uv.lock`, and archives do not bundle packages.
Declare needed checkout dependencies with `uv add DISTRIBUTION` for reproducibility.

## Troubleshooting

| Problem                                        | Fix                                                                                               |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `reqlica` command missing                      | Run `uv run reqlica` from the checkout.                                                           |
| Python version too old                         | Install Python 3.12+ and rerun `uv sync --locked`.                                                |
| Dashboard missing or stale                     | Update to a complete checkout or reinstall the wheel. Rebuild only after frontend source changes. |
| Port already in use                            | Stop the conflicting service or choose different console/gateway ports.                           |
| Project missing from the CLI                   | Check that CLI and servers use the same `REQLICA_HOME`.                                           |
| Model fails to use tools                       | Check credentials, provider URL, model access, and tool-call support.                             |
| Worker reports a missing module                | Use the reviewed package installer, then restart and rerun checks.                                |
| Backend integration tests cannot start workers | Allow local socket connections in the test environment.                                           |

## Frontend development only

If you change frontend source, install Node.js 20.9+ and npm, then run:

```bash
npm --prefix frontend ci
python3 scripts/build_frontend.py
```

Commit the updated `backend/reqlica/static/` files with the frontend changes so
users receive the rebuilt dashboard. `frontend/out/` remains a temporary export.

## Verify the checkout

```bash
uv run python -m unittest discover -s tests
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend test
uv build
```

See [development](development.md) for the CLI and
[limitations](../limitations.md) for runtime constraints.

[Documentation](../README.md)
