# Dashboard development

The dashboard uses Next.js Pages Router, React, and TypeScript. Python serves
the static export and `/api/ui/` adapters; there is no runtime Node.js server.

## Build and run

From the repository root:

```bash
uv sync --locked
npm --prefix frontend ci
python3 scripts/build_frontend.py
uv run reqlica start
```

Open http://127.0.0.1:3000. Rebuild after frontend changes; restart after Python
or configuration changes. A standalone Next.js dev server lacks the Python API
adapters. See [installation](../docs/guides/installation.md) for prerequisites.

## Source layout

- `src/pages/`: page entry points. Projects use `/project?projectId=<id>`.
- `src/components/`: feature panels, layout, and shared controls.
- `src/hooks/`, `src/lib/`: browser state, API calls, and shared helpers.
- `src/styles/` and feature CSS modules: styling and themes.

Follow [AGENTS.md](AGENTS.md) before changing code. Reuse shared controls, preserve
unsaved input on conflicts, and keep draft writes pinned to the loaded revision.
Approval and activation are separate actions. Data insertion needs a current
validated preview and explicit confirmation.

## Browser state

Model profiles/preferences use browser storage without provider keys.
Provider keys live on the backend in private plaintext; explicit **Show key**
can reveal one to the page. Project environment values stay server-side.
Request-header values live in memory and clear on refresh. Data handoff uses
session storage; save datasets or download JSON to retain editor content.
Use hydration-safe hooks before reading browser state, and never execute chat HTML.

## Checks

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
```

`npm --prefix frontend run format` formats frontend files. Node tests cover helpers
and components; Python tests in `tests/test_dashboard.py` cover server adapters.

[Architecture](../docs/architecture.md) · [Documentation](../docs/README.md)
