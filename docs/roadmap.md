# Backend status and product direction

Reqlica turns descriptions/specifications into reviewed Python mock APIs with
persistent SQLite data. OpenAPI specifies contracts, not every business rule;
ambiguous behavior still requires clarification. Once published, requests execute
Python without model calls.

## Implemented backend

- Project CLI/management APIs, independent supervised workers, persistent data,
  startup/shutdown, explicit deletion, and revision-pinned draft approval/activation.
- Saved Project AI conversations and follow-ups through the real MCP transport;
  server-enforced scope, editing/execution permissions, model/tool budgets,
  provider discovery and supported reasoning settings. Groq, OpenAI, compatible
  endpoints and local tool-capable Ollama are supported; Anthropic is preview-only.
- Typed API schemas, draft/published Swagger/OpenAPI, import diagnostics and
  meaningful unittest checks on disposable code/data copies.
- JSON/SQLite/related mock-data previews and separately confirmed live insertion.
- Private application variables and additive database setup.
- [Mock authentication](mock-authentication.md), [scenarios](scenarios.md),
  [rate limits](rate-limits.md), [browser CORS](cors.md).
- [Request/error inspection](inspection.md), including optional bounded redacted
  body and stdout/stderr capture. Capture is disabled by default.
- [SQLite baselines, data restore and counter reset](reset.md).
- [Active project export/import](project-transfer.md), plus stable-ID relocation
  and full version/draft workspace archives through [maintenance](maintenance.md).
- Declarative add/drop/rename column/table and table rebuild migrations, preview,
  disposable draft checks, exact-revision confirmed live apply, durable backups
  and confirmed snapshot rollback. Schema and data limits are documented.
- Installable backend wheel, [installation guide and stateful demo](installation.md).

## MCP authoring boundary

Read tools include project/draft context, API contracts, mock-data metadata,
request history and disposable migration preview. Write tools create/edit one
unpublished draft and prepare synthetic data previews. Validation, tests and
isolated HTTP calls require execution permission inside Project AI.

There are deliberately no AI approval, activation, live migration/rollback,
secret-setting, maintenance or live data-insertion tools. Manual management
operations remain explicit. An exact-revision approval is a local workflow
boundary, not authentication against another process owned by the same user.

## Frontend scope

The packaged Next.js dashboard and frontend integration of newer management APIs
are separate work. This backend status does not imply that all dashboard controls
exist. The standalone HTML dashboard retains the controls described in its guide.

## Operational limits and future extensions

This is a trusted local application. Management APIs are unauthenticated and
Python has OS/network access; workers are not a hostile-code sandbox. License,
remote multi-user authorization, hostile-code isolation, real-backend scenario
execution and a CI runner need separate product/security decisions.

Migrations are declarative, not arbitrary SQL; rebuilds can remove indexes/triggers
and rollback restores snapshot data. See maintenance docs before applying them.
Inspection capture and archive sizes are bounded; log subscriptions/unbounded
payload recording are not implemented. Agent execution advances in explicit steps,
not as an unattended background repair service. Those extensions are not required
for the documented local backend workflow.
