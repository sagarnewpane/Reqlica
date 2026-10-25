# Reqlica dashboard

The management UI uses Next.js Pages Router, React, and TypeScript. Next.js
exports static HTML, JavaScript, and CSS; the Python console serves the result.
There are no Next.js API routes or Node.js server in the packaged runtime.
See the [architecture](../docs/architecture.md) and [technology guide](../docs/technology.md).

## Build and run

Requires Python 3.12+, uv, Node.js 20.9+, and npm on macOS or Linux.

From the repository root:

```bash
uv sync --locked
npm --prefix frontend ci
python3 scripts/build_frontend.py
uv run reqlica start
```

Open http://127.0.0.1:3000. The build script stages `frontend/out/` into
`backend/reqlica/static/`. Rebuild after frontend changes; restart Reqlica after
Python or server configuration changes. The supported full-app workflow uses
the console and its same-origin adapters; a standalone Next.js development server
does not provide those Python API adapters.

Dashboard requests use `/api/ui/` in
[`console/dashboard_api.py`](../backend/reqlica/console/dashboard_api.py).
Console operations dispatch to management routes; runtime operations forward to
the configured gateway. Host/port settings come from the running Reqlica process,
not an independent browser proxy configuration. Project pages use
`/project?projectId=<id>` so newly created projects need no frontend rebuild.

## Source layout

| Directory                               | Responsibility                                                  |
| --------------------------------------- | --------------------------------------------------------------- |
| `src/pages/`                            | Static page entry points, project selection, and loading gates. |
| `src/components/chat/`                  | Composer, saved conversations, tool evidence, and data handoff. |
| `src/components/drafts/`                | Source/routes, checks, database plans, and publication.         |
| `src/components/data/`                  | Dataset preparation, validation, and confirmed insertion.       |
| `src/components/requests/`              | Request tester, chains, and results.                            |
| `src/components/runtime/`               | Workers, traffic/access settings, environment, and packages.    |
| `src/components/projects/`              | Project actions, transfer, and history.                         |
| `src/components/settings/`              | Provider profiles, keys, budgets, connection health, and theme. |
| `src/components/layout/`                | Shared shell and project navigation.                            |
| `src/components/ui/`                    | Shared adapters and generated `shadcn/` primitives.             |
| `src/hooks/`, `src/lib/`, `src/styles/` | Shared hooks, request/state helpers, and theme styles.          |

Keep feature UI and its CSS modules together. Use the shared `PageHeader`,
`WorkerControls`, and `Button` components. Button links use `asChild` with `Link`.
Radix-backed select adapters use `onValueChange`. Follow [AGENTS.md](AGENTS.md)
before changing frontend code.

## Workflow and state rules

- Keep draft writes pinned to the loaded revision. Edits invalidate old checks
  and approval; publishing requires separate approval and activation.
- Preserve unsaved source and form input on conflicts or uncertain responses.
  Refresh/inspect saved state instead of automatically replaying writes.
- Draft edits and code execution have separate conversation permissions. Chat
  does not publish, install packages, set secrets, or insert live records.
- Passed AI data previews stop for review. **Review mock data** transfers the
  artifact to Sample data; live insertion requires current validation and a
  separate confirmation. Failed previews cannot use that handoff action.
- Live requests and published request chains can change persistent records.
  Each disposable draft request gets its own data copy; use behavior tests for
  multi-request flows requiring shared state.
- Use hydration-safe browser hooks before reading browser-only state in output.
  Captured text and model replies must never become executable HTML.

## Credentials and browser storage

Provider keys are app-wide backend secrets scoped to provider and URL, stored in
`$REQLICA_HOME/provider-keys/application.json` with restricted permissions.
They are local plaintext, outside project source, exports, browser storage, and
saved model conversations. Status reports presence only; an explicit **Show key**
action reveals a value to the page. Local Ollama needs no key.

Model profiles and UI preferences use browser storage and contain no provider
keys. Project environment values are private server-side settings. Shared request
header values are held in SPA memory and clear on refresh; saved requests/chains
omit inline header values. Data handoff uses session storage, while unsaved data
previews otherwise live in page memory. Save a named dataset or download JSON to
retain it. Saved datasets can contain user-entered sensitive test data.

## Development checks

Run from the repository root:

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
python3 scripts/build_frontend.py
```

`npm --prefix frontend run format` applies formatting. Generated shadcn and
Next.js files are excluded from formatting; lint/type checking still cover UI
primitives. Python tests cover dashboard adapters and static asset serving:

```bash
uv run python -m unittest discover -s tests -p 'test_dashboard.py'
```

See [feature status](../docs/guides/feature-status.md),
[limitations](../docs/limitations.md), and the [documentation index](../docs/README.md)
for user-facing workflows. The legacy HTML dashboard is a reference rather than
the source of current UI behavior.
