# Frontend parity audit

Comparison: `backend/reqlica/reqlicav1.html` versus `frontend/src`.
This is an implementation audit, not a claim that every legacy feature has been ported.
The legacy Connection screen contains an outdated “Planned” list; the actual controls,
handlers, and current backend routes are the evidence used here.

## UI consistency changes

- One `PageHeader` for compact titles, status/metadata, and adjacent actions.
- Project chat, Runtime, and Endpoints share `WorkerControls`: status, PID, Start,
  Stop, Refresh. Endpoint worker updates preserve the selected route and filters.
- Projects, Drafts, Sample data, Request logs, Request tester, and Settings use the
  same header structure and page gutters. No duplicate project-summary card on Endpoints.
- One button foundation: primary, secondary, ghost, danger; 44px minimum height,
  13px labels, 8px radius, shared hover/focus/disabled/reduced-motion behavior.
  Existing CSS-module actions compose these styles; confirmation dialogs use the
  same Button component. Destructive confirmation actions use danger styling.
- Inputs/selects align at 44px; cards and runtime/settings panels use 20px padding
  and quiet borders rather than different shadows.
- Workflow-specific controls (chat composer, file lists, tabs, dropdown items)
  remain purpose-built rather than being turned into identical buttons.

## Present in the current frontend

Project creation/search/filtering/removal, worker controls, conversations with
pause/resume, provider/model selection and browser profiles, persistent private
project API keys, draft creation/rename/delete, source editing, validation/tests,
approval/activation, saved-draft chat continuation, published and draft OpenAPI inspection, endpoint manifest
browsing, live and disposable draft requests, reviewed database setup, sample-record
validation/export/confirmed insertion, related-record validation/insertion,
request logs/capture settings, environment variables, package review/install.

## Missing or partial workflows

All paths below are relative to `/api/projects/{project_id}` unless stated otherwise.

| Priority | Area | Missing/partial in React frontend | Existing implementation/reference |
| --- | --- | --- | --- |
| P1 | Traffic controls | No delay/failure/empty-response scenarios, route/user rate limits, counter reset, gateway mock-auth credentials, CORS editor, or preflight inspector. Request-header settings are not these features. | `console/routes/scenarios.py`: `/scenarios`, `/mock-auth`; `cors.py`: `/cors`; `reset.py`: `/scenarios/reset`; legacy Traffic controls. |
| P1 | Data baseline/reset | No baseline metadata, capture/replace, or confirmed restore. | `console/routes/reset.py`: `/data-baseline`; `runtime.py`: `/data-reset`. |
| P1 | Project archives | No import, clone, active-bundle export, or full-workspace export. Requires bounded ZIP upload/download handling, not the existing JSON-only proxy. | `console/routes/projects.py`: `/export`, `/api/projects/import`; `maintenance.py`: `/workspace-export`, `/api/projects/workspace-import`. |
| P1 | Migrations | No operation editor, SQL/impact preview, save exact plan to a draft, live apply, history, rollback, or recovery controls. | `console/routes/maintenance.py`: `/database-migrations/*`; legacy Maintenance. |
| P2 | Move/convert project | No project relocation or legacy-to-versioned conversion UI. | `console/routes/maintenance.py`: `/relocate`; `runtime.py`: `/convert`. |
| P2 | Draft route editor | No structured method/path/handler editor, endpoint removal, or file deletion. Source editing offers a manual manifest workaround, not equivalent controls. | `console/routes/drafts.py`: draft `/endpoints` PUT/DELETE and `/files` DELETE; legacy Route definitions. |
| P2 | Contextual draft repair | New chats can select an existing draft. There is no contextual repair button or endpoint-to-source/AI prefill. | `generation/schemas.py`: `AgentRequest.draft_id`; legacy draft Continue/Repair and endpoint edit actions. |
| P2 | AI-generated data handoff | Chat does not render `data_preview` as a reviewable artifact or transfer it into Sample data. Current workaround is copying JSON from text. | `generation/agent.py` returns `data_preview`; legacy “Review mock data” action. |
| P2 | AI context/usage/reasoning | No model-input/tool-schema inspector, token/context usage, reasoning selector/default recovery, or temperature control. Counts alone are not a full context inspector. | `console/routes/generation.py`: `/agent-runs/{run_id}/context`, `/reasoning-default`, `/api/ai/reasoning-options`; agent public `usage`, `context`, `last_model_response`. |
| P2 | Specification attachments | No local JSON/YAML/Markdown/text upload/review/remove flow. Pasting into the chat message remains possible. | Legacy `assistant-attachment` and composer handlers; existing message limits still apply. |
| P2 | HTTP sequences | No ordered request chains, response references, shared headers per chain, or stop-on-failure report. | Legacy `parseRequestChain` / `runRequestChain`: browser orchestration of existing live request bridge. |
| P2 | Request conveniences | No inline per-request headers/overrides or Copy response action. Shared request headers are available under Runtime, not alongside a request. | Legacy tester; current `RequestTester.tsx`, `lib/request-headers.ts`. |
| P2 | Runtime variables | Manual add/replace/remove exists; secure key generation and a convenient replace-existing-variable action are missing. | `console/routes/environment.py`: `POST /environment/{name}/generate`; legacy environment controls. |
| P2 | Package diagnostics/history | Review/install and installed inventory exist, but import-scan issues are not displayed and saved jobs cannot be selected. | `PackagesPanel.tsx` receives `scan`/`jobs`; legacy missing-import list and saved operation selector. |
| P3 | Sample-data conveniences | No template-based record preparation or Copy JSON action. Validation, download, and insertion already exist. | Legacy Mock data controls. |
| P3 | Connection/theme conveniences | No connection-health screen, configurable connection form, dark/light toggle, or convenient live base-URL copy. Backend URLs are configured through server environment variables instead. | Legacy Connection, theme toggle, project-header base URL. |

## Intentional differences to preserve

- API keys are now persistent, project/provider/URL-scoped backend secrets. Status
  does not expose values; an explicit Show key action reveals a saved key on demand.
  Do not restore the legacy browser/request-only key workflow.
- Shared HTTP request headers currently live in SPA memory and clear on refresh;
  the legacy UI used unencrypted browser storage. Treat persistence of bearer tokens
  as a separate security/product choice, not a silent parity change.
- The legacy standalone HTML launcher is unnecessary for Next.js: same-origin API
  routes already bridge to the console/runtime.
- Anthropic-only preview profiles and archived browser-local chats are legacy
  behavior, not functioning equivalents of the current tool-based conversation flow.

## Recommended next implementation slices

1. OpenAPI/Swagger + draft request target + draft database setup (complete the build/review loop).
2. Traffic controls, with revision checks, write-only credentials, and explicit destructive resets.
3. Maintenance: baseline/reset, migrations/history/recovery, archives, relocation.
4. Chat artifacts/context/specification uploads, structured route editing, and smaller conveniences.

Most backend capabilities already exist. Frontend pages, request/response types,
bounded proxy allowlists, revision/conflict handling, and confirmation flows are
the main missing work. Do not add nonfunctional navigation items or expose raw
backend routes without those safeguards. This consistency pass does not implement
the missing workflows listed above.
