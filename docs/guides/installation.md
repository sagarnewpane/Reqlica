# Installation

Requires Python 3.12+, uv, and macOS or Linux. Building from source also requires
Node.js 20.9+ and npm. No container, database service or model
account is needed for manually written APIs or the demo. Generated Python runs
with local OS permissions. Management APIs have no authentication: keep them on
loopback and use trusted source only. This release provides no hostile-code sandbox.

## From a checkout

```bash
uv sync --locked
npm --prefix frontend ci
python3 scripts/build_frontend.py
uv run reqlica start
```

Dashboard/console API/MCP: `http://127.0.0.1:3000`; mock gateway:
`http://127.0.0.1:4000`. Both expose `/docs`. Configure `REQLICA_HOME`,
`CONSOLE_HOST/PORT`, `MOCK_HOST/PORT` through the environment. The source checkout
also supports its root `.env`. Installed wheels should use explicit environment
variables rather than depending on the checkout path.

## Build and install a local wheel

```bash
npm --prefix frontend ci
python3 scripts/build_frontend.py
uv build
uv tool install --from ./dist/reqlica-0.1.0-py3-none-any.whl reqlica
reqlica --help
reqlica start
```

This installs a local artifact, not a published PyPI release. The wheel contains
backend modules, the CLI, and the built dashboard. Node.js is required to build
the frontend, but is not required on machines running the installed wheel.
Dependency installation may need network access unless cached.

## Stateful working demo

```bash
uv run reqlica demo /absolute/new/bookstore
```

The command prints its project ID. With `reqlica start` running, use that ID:

```bash
curl http://127.0.0.1:4000/mock/PROJECT_ID/books
curl -X POST http://127.0.0.1:4000/mock/PROJECT_ID/orders \
  -H 'Content-Type: application/json' -d '{"book_id":1,"quantity":1}'
curl http://127.0.0.1:4000/mock/PROJECT_ID/books
```

The order and stock decrement commit in one SQLite transaction. Missing books
return 404, insufficient stock 409, invalid quantities 422. Data survives worker
restart. The project includes meaningful unittest behavior checks for draft runs;
no AI is called to scaffold or serve it. Use baseline/reset APIs to repeat a chosen
dataset. Project removal follows the existing explicit deletion commands.

## Install missing libraries from the dashboard

Open a project, then **Runtime → Python packages**. Draft check failures also
link to this panel. It scans published source or the selected draft without
executing handlers. Known import/distribution mappings are suggested (for example,
`jwt` → `PyJWT`); unknown names require you to verify the correct distribution.

Enter a distribution and optional exact version, select **Resolve & preview**,
review all resolved versions, then select **Install reviewed packages…** and
confirm the displayed virtual environment. The console needs `uv` on its PATH
and must run in a writable virtual environment. System Python installs are disabled.
The gateway must be running from the same environment; mismatched or unreachable
gateways disable installation so packages cannot be installed into the wrong runtime.

This changes the shared Reqlica environment for every project. Existing versions
are pinned as constraints, so incompatible packages fail resolution rather than
upgrade/downgrade the runtime. Only hash-pinned PyPI wheels are installed; URLs,
local paths, source builds, extras, version ranges, upgrades, and removals are not
accepted by this UI. Installation does not run automatically from generation or validation. External MCP clients can use the same reviewed installer after explicit human authorization; see [MCP package tools](mcp.md#reviewed-package-installation).
Packages can execute code later when imported.

Jobs and approved distribution versions persist privately under the virtual
environment's `.reqlica-packages` directory. Closing/reloading the dashboard does
not cancel a job or replay an uncertain write. Previews expire after 30 minutes;
environment changes require a new resolution. A failed/interrupted installation
may be partial: reload the inventory and resolve again. Every installation attempt
invalidates older draft checks and approval; new checks/publication requests are blocked during
installation. Restart affected APIs from the panel, then rerun validation/tests.
Successfully reviewed distributions become available to Project AI at those
approved versions, including their installed import names.

These installs do **not** edit `pyproject.toml` or `uv.lock`, and project archives
do not bundle Python packages. Recreating/updating the runtime or running an exact
environment sync can remove the additions. For reproducible checkout installs,
also declare the reviewed dependency with `uv add DISTRIBUTION` and commit the
resulting project/lockfile changes through your normal development workflow.

## Verify a checkout

```bash
uv run python -m unittest discover -s tests
uv build
```

Tests include localhost worker processes; environments blocking loopback sockets
cannot run the worker integration suite. Tests use temporary isolated projects.
See [development](development.md), [maintenance](maintenance.md), and
[Project AI](generation.md) for configuration and operational limits.

[Documentation index](../README.md) · [Project overview](../../README.md)
