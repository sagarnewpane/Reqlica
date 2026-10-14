# Reqlica

<img width="640" height="320" alt="Reqlica_Logo" src="https://github.com/user-attachments/assets/f2fd5444-1ecd-4ffc-bc58-2c236579745f" />

A self-hosted mock backend tool for frontend developers and QA engineers.
The goal is to generate stateful Python APIs from descriptions or API specs,
then review, test, and publish them without waiting for a production backend.

## Current Status

The backend supports manually written FastAPI handlers, independent project
workers, persistent SQLite data, and a draft/test/approval/version workflow through
the CLI and management APIs.

AI/MCP generation, the dashboard, database migrations, reset, and import/export
are not implemented yet. See the [roadmap](docs/roadmap.md) for planned work.

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

## Change an API

Create a draft rather than editing published versions:

```bash
uv run reqlica draft create <project-id> --json
uv run reqlica draft inspect <project-id> <draft-id>
```

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
- [Roadmap and design](docs/roadmap.md): planned features, MCP tools, and development order.

**Local trusted use only:** management APIs are unauthenticated. Keep servers on
loopback and only load trusted Python; separate workers are not a security sandbox.
