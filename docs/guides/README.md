# Supporting guides

For the required project documentation, use the [documentation index](../README.md).
These guides cover the current packaged dashboard and Python backend. The older
`backend/reqlica/reqlicav1.html` interface is a legacy reference.

## Setup and API authoring

| Guide                                                 | Contents                                                            |
| ----------------------------------------------------- | ------------------------------------------------------------------- |
| [Installation](installation.md)                       | Checkout setup, local wheel, demo, and reviewed packages.           |
| [Development](development.md)                         | Configuration, CLI/project APIs, checks, and troubleshooting.       |
| [Feature status](feature-status.md)                   | Implemented workflows and where to find them.                       |
| [Frontend](../../frontend/README.md)                  | Dashboard build, source layout, UI conventions, and browser state.  |
| [Project AI](generation.md)                           | Saved conversations, permissions, providers, budgets, and recovery. |
| [MCP](mcp.md)                                         | External client connection, tool catalog, and confirmed operations. |
| [Drafts and versions](persistence-drafts-versions.md) | Source editing, revision checks, testing, approval, and activation. |
| [API schemas](api-schemas.md)                         | Generated OpenAPI, Swagger, and revision-pinned previews.           |

## Data, configuration, and operations

| Guide                                         | Contents                                                       |
| --------------------------------------------- | -------------------------------------------------------------- |
| [Mock data](mock-data.md)                     | Synthetic previews, validation, review, and insertion.         |
| [Database setup](database-setup.md)           | Reviewed additive table plans.                                 |
| [Environment](environment.md)                 | Private project variables and worker restart behavior.         |
| [Mock authentication](mock-authentication.md) | Gateway credential simulation and application authentication.  |
| [Scenarios](scenarios.md)                     | Delays, failures, and empty responses.                         |
| [Rate limits](rate-limits.md)                 | Route and test-user quotas.                                    |
| [CORS](cors.md)                               | Browser origin and preflight policy.                           |
| [Inspection](inspection.md)                   | Request history, errors, capture, and retention.               |
| [Reset](reset.md)                             | SQLite baselines and scenario counters.                        |
| [Project transfer](project-transfer.md)       | Active-project export/import and cloning.                      |
| [Maintenance](maintenance.md)                 | Relocation, full workspace archives, migrations, and rollback. |

All management routes are trusted-local interfaces. External MCP operations are
not automatically available to built-in Project AI.

[Documentation index](../README.md) · [Project overview](../../README.md)
