# Limitations

These are current constraints, not features promised for this release. See the
[roadmap](roadmap.md) for possible improvements and [feature status](guides/feature-status.md)
for implemented workflows.

## Deployment and execution

- Reqlica targets trusted local use on macOS and Linux with Python 3.12+.
  Windows is outside the documented supported setup.
- Management APIs and MCP have no user authentication or multi-user authorization.
  Keep them on loopback. Gateway mock credentials protect simulated client
  requests, not management operations.
- Generated APIs currently run in local worker processes; draft checks use local
  subprocesses. There is no integrated execution sandbox. Generated Python has
  the local user's filesystem, environment, and network access. Disposable SQLite
  copies isolate check data but do not restrict code execution. A future sandbox
  integration could provide stronger execution isolation.
- Generated APIs currently use Python and FastAPI only. Other languages and
  backend frameworks are not supported.
- Reqlica currently runs locally; it is not a hosted SaaS platform. A hosted
  experience similar to Lovable would require sandboxed execution, tenant
  isolation, management authentication, resource limits, and hosted persistence.
- Python dependencies share one environment. Reviewed package installation can
  affect every project; archives do not bundle dependencies.
- Source builds require Node.js/npm and a dashboard export before packaging.
  Installed wheels serve static assets without a Node.js server.

## AI behavior and verification

- API authoring requires a tool-capable model and a configured supported provider
  or local Ollama. Anthropic profiles are not supported by the current backend
  conversation API. Provider cost, latency, tool support, and quality vary.
- Requirements and OpenAPI do not describe every business rule. Ambiguities need
  clarification. Generated code and tests still require human review; passing
  checks prove only the behavior they exercise.
- Each step advances one model request or tool call. Browser-driven advancement
  stops after an in-flight step when paused or closed; this is not an unattended
  background job service. Uncertain interrupted writes are not replayed blindly.
- AI request payloads, tool schemas, and project/conversation context are
  currently relatively large. They can increase token usage, latency, and provider
  cost. Further optimization could reduce their size while retaining the context
  needed for correct tool use and API generation.
- Default conversation budgets are 24 model calls, 16 model calls per turn,
  64 tool calls, and 100,000 provider-reported tokens. Explicit budget updates
  preserve recorded usage; they do not reset it. Model/tool limits allow 1–1,000,
  and token budgets allow 1,000–1,000,000. A prompt also has a 512 KiB byte bound.
  Provider quotas/context windows can be lower; byte counts are not token counts.
- Three consecutive tool failures stop automatic repair. Repeated finish attempts
  without progress are bounded. A passed data preview stops immediately for
  review; it is not approval to insert records.
- Selected source and tool output go to the chosen provider. Runtime secrets and
  existing rows are excluded from ordinary mock-data context, but trusted Python
  can expose values in results and logs. Checkpoints retain prompts and tool data.

See [Project AI](guides/generation.md) for current API fields and recovery behavior.

## Data and contracts

- Mock-data batches allow at most 100 records and 128 KiB; related batches use up
  to 10 existing tables. They append records rather than overwrite existing rows.
  SQLite previews must pass again after edits, schema changes, or code changes.
- The importer refuses unsupported password/hash, encrypted, binary, custom
  validator, and storage formats rather than inventing plausible values. Use the
  application's signup/create endpoints for those records. Existing foreign-key
  IDs must be supplied or verified by the user; new related rows can use `$ref`.
- Validation uses a snapshot, not reserved IDs or unique values. Insertion rechecks
  current state atomically. Inserts are not idempotent; inspect live data after a
  lost acknowledgement before attempting another insertion.
- There is no generic live-record CRUD browser. Unsaved previews are temporary;
  save a named dataset or download JSON before leaving the screen.
- Additive schema plans only create supported ordinary tables. Changes to existing
  tables use declarative migrations, not arbitrary SQL. Rebuilds can remove
  indexes/triggers. Rollback restores snapshot records and can discard later writes.
- Restoring a code version does not restore its old database. Separate draft
  requests discard data changes; use a single behavior test for stateful sequences.
- OpenAPI comes from loaded FastAPI handlers. Undeclared business rules and
  unsupported imported schema constructs cannot be inferred as verified behavior.

See [mock data](guides/mock-data.md), [database setup](guides/database-setup.md),
[maintenance](guides/maintenance.md), and [API schemas](guides/api-schemas.md).

## Operations and persistence

- Activation can briefly make a project unavailable while traffic drains and the
  candidate worker starts. Direct external SQLite writers are outside worker
  coordination and must be managed separately.
- Request history is bounded and best effort, not a complete audit log or live
  stream. Optional capture redaction is also best effort.
- Project archives are bounded to 32 MiB compressed and 128 MiB unpacked, with
  2,000 content files. Full workspace restore preserves identity; active bundles
  support cloning. Neither format captures external services or arbitrary globals.
- Backups, historical snapshots, and saved conversations consume disk; there is
  no general automatic retention/pruning service.
- No remote team workspace, real-backend scenario runner, or dedicated CI runner
  is included in the documented local workflow.

[Documentation index](README.md) · [Project overview](../README.md)
