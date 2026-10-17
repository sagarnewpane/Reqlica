# Reqlica standalone dashboard patterns

## Scope and direction

- Applies to `backend/reqlica/reqlicav1.html`, not the separate Next.js frontend.
- Developer workbench for inspecting contracts, preparing real requests, and
  reviewing unpublished drafts. Technical, calm, compact, and explicit about risk.
- Preserve the existing single-file implementation and native HTML controls.
  Reuse its classes and semantic tokens rather than introducing a new UI system.

## Visual system

- Dark slate workspace by default; retain the existing light theme.
- Borders and subtle surface differences provide depth; no decorative gradients
  or prominent shadows. Sidebar shares the workspace background.
- Surfaces: `--workspace` canvas, `--panel` grouped content, `--inset` inputs,
  `--terminal` source/response output. Text: `--ink`, `--secondary`, `--muted`.
- Green `--request` marks primary actions and success; amber `--draft` distinguishes
  unpublished/local-preview states and cautions; `--error` denotes failures.
  Secondary helpers stay neutral. Use `--line`, `--control-line`, and `--focus`
  for separation, control boundaries, and keyboard focus.
- Sans: `--sans` (IBM Plex Sans/Avenir stack). Code, paths, IDs, and JSON use
  `--mono` (JetBrains Mono/SFMono stack).
- Existing hierarchy: body 15px/1.6; help/code/buttons 13px; h3 16px; h2 21px;
  h1 30px, weight 600 (26px on mobile). Preserve weight/color hierarchy, not
  just size differences. Primary actions should dominate their secondary helpers.
- Spacing uses a 4px rhythm: action gaps 8px, field-row gaps 16px, field groups
  20px, panel padding/section gaps 24px. Keep existing control padding rather
  than normalizing unrelated styles.
- `.btn`: minimum height 44px, padding 9px 14px, 6px radius, 13px/600.
  `.btn.primary` is green; ordinary `.btn` is the bordered secondary action;
  `.btn.ghost` is borderless navigation/utility.
- Inputs/selects/textareas: minimum height 44px, padding 10px 12px, 6px radius,
  inset surface. Panels use `--radius` (10px); panel padding becomes 20px on mobile.
- At 760px and below, stack field rows and use existing mobile navigation.
  Request-form rows own their 20px bottom spacing; child fields have no bottom
  margin, preventing uneven desktop spacing and touching mobile controls.

## Request preparation pattern

- **Send request** remains the primary action. Place neutral **Insert example**
  beneath the target/route controls, ahead of method/path/body inputs.
- Pair the helper with **From OpenAPI · does not send** and a nearby help/status
  message (`role="status"`, referenced by the button's `aria-describedby`).
- Require a selected route. Read the selected target's saved OpenAPI afresh;
  pin draft schema reads to the selected draft revision. Do not validate, run
  handlers, start workers, invoke AI, or send the example automatically.
- Prefer documented examples, then defaults/types. Resolve local schema references
  only. Sample values and record IDs are suggestions, not business-rule guarantees.
- Fill method, path/query, and JSON body. Preserve custom headers. The draft call
  API already supplies its revision pin; do not ask users to enter that header.
- Confirm before replacing edited path/method or a nonempty body. Cancellation,
  unavailable/stale contracts, and unsupported shapes preserve all request inputs.
- Explain missing prerequisites: drafts need validation of the saved revision;
  published contracts need validated activation. Never silently use another
  target's schema or infer a missing contract.
- Keep live writes behind explicit confirmation; clearly distinguish persistent
  live data from revision-pinned disposable draft calls.

## Project AI workspace

- Keep questions and generation in the same **Generate API** screen, with an
  explicit **Generate draft / Ask about project** selector. Never infer permission
  to modify a project from a question; Ask mode only calls the read-only `/ask` API.
- Reuse the provider selector and established two-column input/output panels.
  Preserve each mode's input and result independently in per-project page memory.
- Ask mode shows a plain-text explanation, the exact answered question, source
  references, snapshot revision, and limitations. Treat model text as untrusted:
  render text content or escaped strings, never raw model HTML.
- Confirm external context sharing for each request. State that each question is
  independent, published context excludes draft changes, and no code or data is
  changed. Keep generation's separate draft confirmation and review workflow.

## Verification

- Check keyboard focus, disabled/loading/error/cancel states, desktop and 390px
  mobile layouts, and both themes when relevant. No horizontal overflow.
- Example helper tests: `tests/html_request_examples.cjs`, run directly with
  `node --test` or via `tests/test_html_request_examples.py`.
- Assert insertion only reads the contract: no endpoint execution, credential
  persistence, automatic validation, or automatic publication.
