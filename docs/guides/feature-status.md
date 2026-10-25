# Feature status

This page describes implemented local workflows as of 2026-10-25. It replaces
older frontend parity and remaining-feature checklists. Implementation does not
mean every model, operating system, or configuration has been tested.

| Area                     | Current workflow                                                                                                               | Reference                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| Project management       | Create, discover, start/stop, unregister, and explicitly delete project files.                                                 | [Development](development.md)                                  |
| Dashboard delivery       | Static Next.js export served by the Python console; no Node.js server at runtime.                                              | [Architecture](../architecture.md)                             |
| Project AI               | Saved MCP-backed conversations, specifications, clarifications, edits, diagnostics, repairs, and explicit budgets/permissions. | [Project AI](generation.md)                                    |
| External agents          | Streamable HTTP tools for authoring and inspection; selected maintenance tools require human confirmation.                     | [MCP](mcp.md)                                                  |
| Drafts and publication   | Source/route editing, validation, behavior tests, exact-revision approval, and separate activation.                            | [Drafts and versions](persistence-drafts-versions.md)          |
| API contracts            | Generated draft/published OpenAPI and Swagger, plus specification import/export.                                               | [API schemas](api-schemas.md)                                  |
| Sample data              | JSON, SQLite, and related-record validation; AI previews pause for review; editable datasets and confirmed atomic append.      | [Mock data](mock-data.md)                                      |
| Database setup           | Reviewable additive table plans checked on disposable copies before confirmed live application.                                | [Database setup](database-setup.md)                            |
| Existing schema changes  | Declarative migration preview, draft checks, confirmed apply, history, recovery, and snapshot rollback.                        | [Maintenance](maintenance.md)                                  |
| Environment and packages | Private project variables; secure key generation; reviewed shared-environment package installation.                            | [Environment](environment.md), [Installation](installation.md) |
| Traffic simulation       | Delays, finite/persistent failures, empty responses, and route/test-user quotas.                                               | [Scenarios](scenarios.md), [Rate limits](rate-limits.md)       |
| Client access            | Gateway bearer/API-key simulation and per-project CORS. Generated handlers can implement separate application authentication.  | [Mock authentication](mock-authentication.md), [CORS](cors.md) |
| Request testing          | Live/disposable requests, contract examples, header overrides, saved requests, and ordered request chains.                     | [Frontend guide](../../frontend/README.md)                     |
| Inspection               | Persistent bounded request/error history and opt-in payload/output capture.                                                    | [Inspection](inspection.md)                                    |
| Reset                    | SQLite baseline capture/restore and separate scenario counter reset.                                                           | [Reset](reset.md)                                              |
| Transfer and history     | Active ZIP clone/import, full workspace backup/restore, relocation, and historical code restore into a draft.                  | [Transfer](project-transfer.md), [Maintenance](maintenance.md) |
| Settings                 | Shared backend provider keys, browser model profiles, model discovery, theme, and service health.                              | [Frontend guide](../../frontend/README.md)                     |

Tests live in `tests/`. Run the documented commands to assess the current checkout;
this status page does not certify a passing full suite. For constraints and planned
work, use [limitations](../limitations.md) and the [roadmap](../roadmap.md).

[Documentation index](../README.md) · [Project overview](../../README.md)
