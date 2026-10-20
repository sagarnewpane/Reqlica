# Project AI: conversations and endpoint generation

Reqlica's dashboard reviews requirements with Groq, asks clarification questions,
and generates a draft only after a separate confirmed generation action. Each
review operation makes one provider request. Dashboard generation uses durable
plan/file checkpoints with bounded retries. The legacy generation and endpoint
editing APIs allow one correction for malformed JSON or response-schema errors.
It uses the same
`get_project_context()` helper as MCP. Read-only project chat also accepts bounded
recent conversation history and refreshes project context for each reply. The
server stores no chat conversations. Generation checkpoints retain the requirements
and intermediate source. Generated code is never executed by the generation flow. Draft generation
remains separate and does not automatically consume chat history. Failed draft
checks now have a separately confirmed, bounded repair-and-retest workflow.

## Try it in the HTML dashboard

1. Configure `GROQ_API_KEY` in the repository-root `.env` or environment and
   restart `uv run reqlica start`. The existing `GROQ_API` name also works.
2. Launch `backend/reqlica/reqlicav1.html` through the local server described in
   its **Connection & help** section.
3. In **Settings**, add a **Groq** profile. Use
   `https://api.groq.com/openai/v1` and `openai/gpt-oss-20b` (default) or
   `openai/gpt-oss-120b`. A browser key is optional when the console has one.
4. Open a versioned project and **Generate API**. Start with a small stateless
   request, such as a typed greeting endpoint and HTTP tests.
5. **Preview generation brief** stays local. **Review requirements** shares context
   only after confirmation, asks about ambiguous fields/configuration/behavior,
   and creates no draft. Choose suggestions or type decisions—not secret values.
   Optionally **Review answers with AI…** for follow-up questions. Once answered,
   explicitly **Generate from answers** (or **Generate draft** if none were needed).
6. Review the returned summary, assumptions, and draft files. Only after review,
   explicitly run **Validate**, **Run tests**, and disposable requests. Fix failed
   code/tests manually; success is not assumed from a valid JSON response.
7. Approval remains CLI-only and activation remains explicit. Nothing is live
   merely because generation succeeded.

Model, temperature, and maximum output tokens are used for real generation.
This initial generator always requests typed contracts, tests, and assumptions;
the other preference checkboxes still apply only to UI previews. Other provider
types remain previews. Endpoint editing now saves real drafts; custom mock data
supports JSON previews/downloads and confirmed append-only inserts into existing
SQLite tables. See [mock-data.md](mock-data.md).

## Dependencies and missing-package errors

Clarification and generation share a `runtime_dependencies` policy in project
context: the current Python and package versions, available import names/distributions, missing
declared modules, and the SDK path. AI can use the standard library, local modules,
`reqlica.runtime.sdk`, FastAPI/Pydantic/httpx, and installed **direct, unconditional
Reqlica dependencies**. Incidental/transitive installs are not automatically
approved. Optional libraries such as `bcrypt` must be explicitly added before AI
can use them; the policy reflects the actual installed, declared dependencies.

Generated handlers **and tests** receive AST import preflight before any draft is
created. Unsupported imports and missing local modules return HTTP `502` with
`code: "dependency_error"`, a readable message, and bounded `dependencies` entries
containing `path`, `line`, `module`, `code`, and `message`. Code is not imported or
executed by preflight. No draft is saved and no automatic retry/repair is made.
Explicitly requesting another library does not bypass the policy.

Generation, repair, and draft checks also use non-executing Pyflakes name analysis
to catch undefined variables, missing annotation imports, and unbound locals,
including inside handler branches not exercised while loading the app. Existing
draft failures include `source_issues` with file/line locations. Style and unused
imports are not validation failures. Dynamic names and imported symbols still need
runtime/behavior checks.

Tests must actually execute assertions: async methods require
`unittest.IsolatedAsyncioTestCase`, and returning a boolean/value from a test is an
error, not an assertion. Zero passing tests still fails. Validation loads routes
and generates OpenAPI; it does not replace behavior tests. Prompts provide runtime
versions, explicit test-configuration guidance, and shared implementation checks;
repair receives the source diagnostics alongside compacted failure evidence.

For an existing draft, **Validate** and **Run tests** report missing static imports
before loading handlers. Manual drafts can still use installed libraries outside
the AI policy; optional/type-only imports are left to runtime. Dynamic imports or
missing submodules can still fail at runtime; missing-module startup/runner errors
now explain the missing dependency instead of returning only a traceback. Preflight
does not resolve imported attributes, prove behavior, or sandbox trusted Python.

Adding packages remains an **operator decision**, not an AI action or dashboard
install button. Review the distribution and its effect on all projects, then, for
example, run from Reqlica's repository root:

```bash
uv add bcrypt
# Import name jwt belongs to distribution PyJWT, not a package named jwt:
# uv add PyJWT
```

Restart Reqlica to refresh its dependency metadata/policy, refresh the dashboard,
review requirements again if its dependency fingerprint changed, and rerun both
checks on the draft. Existing failed drafts are preserved and need not be recreated.
`uv add` records the explicit dependency and updates `uv.lock`; `uv pip install`
alone is not approval for AI use. Unknown import names never become guessed install
commands. Conditional/extra dependencies are not automatically offered to AI.

## Console API

### Repair a failed draft

In **Drafts & checks**, run validation/tests, then use **Repair with AI…** beside
the failures. Choose a Groq provider and a limit of one or two attempts. Confirmation
authorizes sharing draft source, original requirements/confirmed answers, test
source, schema definitions, configured variable names, and redacted failure logs,
then saving implementation and evidenced test corrections and executing disposable checks. This is a
real product workflow; no assistant session or manually supplied patch is needed.

New generated drafts retain requirements and clarification decisions in local
draft metadata (`generation`). Older/manual drafts require the original spec to
be pasted once; the system must not guess it from a traceback. Requirements cannot
be replaced through repair. No secrets should be entered into requirements.

Each `POST /api/projects/{project_id}/drafts/{draft_id}/repair` performs **one**
provider call, saves a valid candidate, validates it, and runs tests if validation
passes. The dashboard repeats only on actual `checks_failed`, within the confirmed
limit. Provider errors, timeouts, invalid proposals, missing information and revision
conflicts stop immediately; they do not trigger another provider request.

```json
{
  "expected_revision": "<current failing revision>",
  "confirm": true,
  "requirements": "<original spec; optional when already saved>",
  "model": "openai/gpt-oss-120b",
  "temperature": 0,
  "max_tokens": 8192
}
```

The standard optional `X-Reqlica-Groq-Key` header applies. HTTP `200` results are:

- `repaired`: validation and behavior checks passed. Still unapproved/unpublished.
- `checks_failed`: candidate saved; exact-revision check output remains available.
- `needs_information`: no files changed; blockers describe a needed decision,
  configuration, dependency approval or unsupported operation.

Changed results include `draft`, `changed_files`, bounded unified `diff` (32 KiB,
  with a truncation flag), validation/tests and provider usage. The latest summary,
  originating revision, changed paths, coverage floor and code backup location are
  retained as `draft.repair`; the full in-page attempt transcript/diffs are not
  persisted in browser storage. Reloading shows the saved latest summary/checks.

Repair protects route registration, schema plans and original requirements.
Canonical `.py` implementation files in `endpoints/` and `models/`, plus existing
Python modules in `tests/`, may change. Test corrections must address demonstrated
fixture or assertion-API defects against the original contract, such as overlong
success-path usernames or passing JSON strings to datetime methods. The response
and saved repair metadata identify `changed_test_files`; inspect these in the
repair diff before approval. It runs the same dependency preflight as generation and
never installs packages or changes environment settings. A code-only backup is
saved under `.reqlica/repair-backups/<draft-id>/<previous-revision>/` before writes.
Concurrent source, environment, published-version or database-definition changes
abort the attempt. Failed candidates are kept for review, not silently rolled back.
Interrupted writes leave a draft/backup to inspect; refresh before retrying.

An initial passing-test-count floor is retained across attempts/check reruns so
skipping previously exercised tests cannot produce a passing repair. Structural guards preserve test identities, signatures, class bases/decorators,
assertion counts/types, and HTTP status assertions. Added skips, early returns
and exception handlers in tests are rejected. AI must preserve rejection/boundary
cases and explain corrections using contract/failure evidence. A genuine conflict
between confirmed behavior and the saved contract still requires a contract change
outside repair. Correcting faulty tests is allowed; weakening the API is not. These checks cannot prove semantic correctness
or prevent all malicious runtime monkeypatching. Python is **not sandboxed** and
has local OS/network privileges: execute only trusted code. SDK DB writes use
disposable copies, but arbitrary Python can bypass that boundary. Review source,
contracts and test quality before CLI approval. Database setup and activation
remain separate confirmed operations; repair never publishes code.

One attempt fits the existing request timeout budget; the dashboard's loop uses
separate revision-pinned requests. Closing the page does not necessarily cancel
an in-flight backend request, but no further browser-driven attempts are launched.

### Clarify requirements before generating

`POST /api/projects/{project_id}/clarify` accepts generation `requirements` and
model settings. For an endpoint edit, also supply its exact `endpoint` and
`expected_revision` from `/endpoint-source`. Optional `X-Reqlica-Groq-Key` applies
as usual. This is a read-only planning request: no files/drafts, execution, data
writes, approval, or activation.

The dashboard uses **Settings → AI defaults → Maximum output tokens** for both
requirements review and generation, including endpoint edits. Review requests do
not apply a separate token cap.

AI requests in the dashboard show elapsed time and confirmed activity from the
console: request accepted, contacting the provider, HTTP headers received,
answer received, response checks, and completion or failure. These are activity
signals, not model reasoning or a percentage estimate. While waiting, the UI
does not claim the model is thinking or producing tokens. Missing activity
updates are shown separately from failure of the original request.

The dashboard sends a random `X-Reqlica-Activity-ID` header and polls
`GET /api/projects/{project_id}/activity/{activity_id}` during the request.
Tracking is optional for API clients and does not change the original response
or retry requests. Only stage names and elapsed milliseconds are retained in
process memory (15-minute TTL, up to 128 requests and 64 events per request).
Prompts, keys, source and model content are excluded. Records are scoped to a
project and request ID, are not cached, and disappear on console restart. Use
the default single console worker so polling and generation share the registry.

**Settings → AI defaults → AI response timeout** controls the provider read wait
for every AI flow, including review, generation, chat, endpoint edits, mock data
and each repair attempt. Default: **300 seconds**; configurable range: **30–600**.
API clients can set `timeout_seconds` in the request body. Older saved browser
preferences receive the 300-second default automatically. Connection/pool waits
are bounded at 10 seconds and writes at 30 seconds. These are network inactivity
limits, not a guarantee that the entire operation ends within that duration.

For AI requests the embedded proxy allows the configured wait plus 30 seconds;
the dashboard allows plus 60 seconds. With defaults these are 330 and 360 seconds,
respectively. `X-Reqlica-AI-Timeout-Seconds` carries the dashboard's selected
budget to the local proxy and is not forwarded upstream. Other dashboard requests
keep their existing timeouts. Activity polling uses its own short timeout and
continues throughout the original request. No layer automatically retries.
If the dashboard or bridge times out, backend work can still finish: refresh
drafts/current state before retrying. These requests remain attached to the page;
reload/navigation does not provide background job recovery.

### Reasoning controls

Edit a profile in **Settings → LLM providers** and use **Check model support**
beside **Reasoning**. Select **Provider default**, **Off** when the model permits
it, or one of its supported effort levels. The choice is saved with that browser
profile and used for review, generation, chat, edits, data and repair requests.
Provider default preserves existing behavior (including low effort on Groq).

OpenRouter capabilities come from its public `/api/v1/models` catalog, cached
for 60 seconds, without sending keys, prompts or project context. A mandatory
reasoning model never offers Off. Unknown models/custom endpoints offer only
Provider default. Direct OpenAI controls use conservative documented model
allowlists; unrecognized models keep defaults. Changing provider/model resets
the selection and requires a fresh support check.

API clients use `reasoning_mode`: `default`, `off`, `minimal`, `low`, `medium`,
`high`, `xhigh` or `max`. Non-default selections are checked server-side too;
unsupported modes return 422 and catalog failures return 503 before a model
request is sent. OpenRouter Off sends `reasoning.enabled: false`, effort choices
send `reasoning.effort`, and supported direct OpenAI models use
`reasoning_effort` (`none` for Off). Hiding a reasoning field is not used as a
substitute for disabling reasoning. The provider may still reject a request if
its capabilities change between lookup and execution; no automatic retry occurs.

### Provider output formats

OpenRouter requests check the same cached public catalog for format support.
Models advertising `structured_outputs` receive strict JSON schema requests.
Models advertising only `response_format` use JSON mode; other OpenRouter models
receive text requests with the complete schema in their instructions. Supported
format parameters are required for routing. All modes receive the same local
validation and bounded correction handling. Direct APIs request strict schemas. The support check and
activity timeline identify the selected format. Metadata lookup failures stop
before generation rather than silently downgrading a supported schema request.

Serialization instructions include one combined files array, separate objects for
each file/route, correctly escaped source strings, and a two-file serialization
example. This improves compliance but cannot guarantee it on unconstrained
endpoints. Local schema and duplicate-key validation remain mandatory, including
under strict output modes. Nested duplicate errors identify the affected array
entry/known field without exposing generated code or arbitrary model keys; no
values are discarded or guessed. For generation and endpoint editing, a single
enclosing Markdown fence can be removed and invalid string escapes/control
characters normalized before validation. Trailing commas outside strings can
also be removed. In the legacy generation/edit APIs, structural JSON and schema
errors trigger at most one correction:
the failed response is sent to the same provider as explicitly untrusted data,
along with the original requirements and instructions to preserve the implementation.
This may incur a second provider charge and response wait.
Usage includes both attempts when available. The replacement must pass all
schema and static checks before a draft is saved. Network errors, truncation,
and source validation failures do not trigger repair in those legacy APIs.
If the corrected response fails validation, the error includes `attempts: 2`
and a failure code; invalid source is never saved as a draft.

HTTP `200` returns:

- `status`: `needs_clarification` or `ready` when no questions remain.
- `summary`, and up to eight `questions`, each with `id`, `kind`
  (`field`, `environment`, or `behavior`), `question`, and up to six suggested
  `options`. Free-form answers are supported; fully explicit specs need no
  redundant questions.
- `context_fingerprint`, `context_revision`, provider usage, `read_only: true`,
  and any `prior_decisions` preserved from an explicitly requested follow-up review.

For `/generate` or `/edit-endpoint`, add optional `clarification`:

```json
{
  "context_fingerprint": "<fingerprint from review>",
  "decisions": [
    {
      "question": {
        "id": "jwt_config",
        "kind": "environment",
        "question": "Which JWT configuration name and missing-setting behavior?",
        "options": ["JWT_SECRET; return 503 if missing"]
      },
      "answer": "JWT_SECRET; return 503 if missing"
    }
  ]
}
```

Use the exact question objects/order from the actual review, not the illustrative
question above; prepend `prior_decisions` if returned. Every question needs a
nonblank answer (up to 4,096 characters), and at most eight decisions are retained.
Missing/reordered/changed questions, changed requirements/source/schema, or changed
configured environment-name lists or runtime dependency policy require a fresh review (`409`) before a provider
generation call. Answer validation errors return `422`. The existing 96 KiB full
prompt bound includes all decisions; nothing is silently truncated.

Suggested answers are optional: `options: []` supports free text, while each
suggestion must contain non-whitespace text and be at most 512 characters. These
constraints are included in the provider's strict structured-output schema and
validated again locally. The clarification prompt includes complete examples of
string suggestions and a free-text-only question, and explicitly prohibits nested
arrays: `options: ["Yes", "No", []]` is invalid. Invalid provider reviews return
`502`; no draft is created and no automatic retry is made.

Provider errors distinguish output-schema failures, context limits, and exhausted
output budgets. Technical details include the operation, model, prompt byte count
(not a token estimate), output budget, and upstream HTTP status. When Groq supplies
a failed JSON response, bounded validation diagnostics expose only known field
paths and fixed reasons, never generated values, unknown field names, or raw
provider messages. An empty implementation is displayed as “No implementation
returned,” since it can represent a scope refusal as well as a missing decision.

The dashboard blocks generation until all current questions are answered. It also
supports explicit follow-up reviews, preserving answered decisions in expandable
details. Inputs, choices, and pending decisions are held in page memory, not saved
with chat history. Changing requirements/provider invalidates the current review.
Server fingerprints protect against accidental stale/mixed answers, not malicious
clients; this is not an approval/authentication gate. Direct API clients can still
use the legacy single-call generation API without a review. Generation is prompted
to honor supplied decisions and ask again rather than invent critical behavior.

Environment questions ask about variable **names**, configuration workflow, and
missing-value behavior—never actual keys/passwords. Use **Settings → Project
environment** for values. The model sees configured names only, not private values.

### Ask about the project (read-only)

In the same **Generate API** screen, switch to **Ask about project**. For example:
"How is POST /books designed? Explain its inputs, rules, and data relationships."
Select a Groq profile and confirm sharing context at the start of the conversation.
Follow-up questions use the same consent until the provider/settings change, the
page reloads, or **New chat** is used. Each message makes one provider request;
there is no automatic retry. Replies appear in a conversation, with source
references, caveats, and snapshot revisions collapsed beneath each reply. Context
and privacy details are also collapsed; actual failures remain visible. Switch
back to **Generate draft** to request implementation; Ask mode cannot create drafts
or execute changes, even if the question asks it to.

```http
POST /api/projects/{project_id}/ask
Content-Type: application/json
X-Reqlica-Groq-Key: <optional per-request key>

{
  "question": "How is GET /hello implemented and tested?",
  "history": [],
  "model": "openai/gpt-oss-20b",
  "temperature": 0,
  "max_tokens": 4096
}
```

HTTP `200` returns `status: "answered"`, `answer` (plain text), `sources`,
`limitations`, `context_revision`, `history_used`, provider/model/usage, and a
read-only warning.
Source references must identify supplied Python/tests, available `manifest.json`
or `PROJECT_CONTEXT.md`, `database_definitions`, or `sdk`; unknown references are
rejected. References are checked for existence, not proof that the explanation is
correct. Answers do not establish runtime health or successful tests, and inferred
design rationale may be wrong. They describe the snapshot read before the model
request, not changes made while the provider was responding.

Questions use the same context helper, privacy boundaries, context/provider limits,
and credentials as generation. Questions are limited to 16,384 characters; answer
text to 32,768 characters, with up to 50 references and 20 limitations. The dashboard uses
your saved model/temperature/token settings; the API's default answer budget is
4096 tokens. It makes one provider call, without retry, repair, or fallback.

#### Conversation history and privacy

- `history` is optional and defaults to `[]`. It accepts up to **eight complete
  exchanges**, each with `question`, `answer`, and the answer's `context_revision`
  (64 lowercase hex characters). Arbitrary roles, system/tool messages, blank
  exchanges, extra fields, and invalid revisions are rejected with HTTP `422`.
- History is limited to **48 KiB of compact UTF-8 JSON**, as well as the shared
  **96 KiB complete-prompt limit**. History within its own limit can still exceed
  the combined limit alongside a large project; that returns `413` before any
  provider call. The server never silently truncates an API request.
- The dashboard retains up to **20 completed exchanges / 512 KiB per project** in
  this browser tab's `sessionStorage`. It survives refreshes and project navigation,
  not normal tab closure. Browser session restore/duplicating tabs may retain/copy
  session data: use **New chat** to explicitly remove it. Completed replies may
  contain excerpts of source; only use this on a trusted browser. Credentials,
  unsent messages, and whole provider/workspace objects are never stored with chat.
- The dashboard sends the most recent contiguous exchanges fitting eight turns /
  48 KiB, in chronological order. The conversation footer reports how many replies
  will be included next. Older exchanges can remain visible without being sent.
  Reaching the retention limit discards the oldest displayed/saved exchanges.
- Histories are separated by project ID and configured gateway/console addresses.
  **New chat** clears only that project's conversation after confirmation, resets
  sharing consent, and leaves project files, data, and drafts unchanged. Deleting
  a project through the dashboard also removes its tab history. If session storage
  is denied or full, the history remains in page memory and Context & privacy
  reports the fallback. Failed/cancelled requests do not add exchanges or clear
  the composer's unsent message.
- Historical questions/answers are untrusted reference data in the user payload,
  never privileged system instructions or proof of behavior. Each exchange retains
  its snapshot revision; freshly read published code overrides older claims. No
  server-side session, history database, or conversation mutation endpoint exists.

### Edit an existing endpoint

In **Endpoints**, select a route and choose **Edit source** to create a fresh draft
with its handler file loaded into the existing source editor. Save, inspect tests,
validate/test, and obtain CLI approval before activation. A draft is created even
if you subsequently leave the editor without changing it; existing drafts are
never silently reused or overwritten.

**Modify with AI** loads the published source revision, takes a change request,
and saves the result into a new unexecuted draft after context-sharing confirmation.
The method/path must stay unchanged. The proposal can select a different handler
function, but it must preserve other route handlers, including shared originals.
Unrelated registrations, stale source revisions, missing tests/handlers, and
invalid Python are rejected before draft creation. Shared model/import changes
can still affect behavior: these guards do not replace human review and tests.
Renaming routes or broader redesigns belong in **Generate API** or the manual editor.

- `GET /api/projects/{project_id}/endpoint-source?method=GET&path=/hello` returns
  the registered `endpoint`, published `revision`, handler-file `path`, and `content`.
  It reads source only; it does not import or run handlers.
- `POST /api/projects/{project_id}/edit-endpoint` accepts the same provider settings
  and `requirements` as generation, plus the exact `endpoint` object and
  `expected_revision` from the source read. It returns HTTP `201` with the normal
  `draft_created` result, `operation: "endpoint_edit"`, and `edited_endpoint`.
- The optional `X-Reqlica-Groq-Key` header and privacy rules are unchanged. No
  automatic retries, validation, approval, activation, or live code changes occur.

#### When generation needs more information

Both `/generate` and `/edit-endpoint` may return HTTP `200` with
`status: "needs_information"` instead of HTTP `201` / `draft_created`. This happens
when Groq returns an explanation with both `files` and `endpoints` empty—for
example, a database credential check with no users table or password-hash scheme.
The response preserves `summary`, `assumptions`, `context_revision`, provider
usage, `output_warnings`, and `checks: "not_run"`; it contains **no draft**.
Endpoint edits also identify `operation` and `edited_endpoint` as usual.

The dashboard displays the model's explanation and questions as plain text, keeps
the request editable, and does not show a draft-review link. Add the missing
details and explicitly generate again; this is not an automatic retry or chat
continuation. Missing tables can now be proposed in `database/schema.json` and
explicitly applied through [Database setup](database-setup.md); unsupported
alterations and missing design decisions still need clarification. Application
secrets can now be configured through [Project environment](environment.md),
without sending their values to the model. An output with
only one empty array is still an invalid partial implementation, not a clarification.
Explanations are model-generated, not verified diagnoses; no code/data is changed.

### Generate a new endpoint

Database-backed generation may include `database/schema.json` in `generated_files`.
It is a declarative plan, not a live DB or executable migration. The response's
`database_setup` field identifies a returned plan requiring review. Draft checks
prepare its tables only on disposable copies. Apply the reviewed plan explicitly
before activation; the generator never runs schema changes itself.

```http
POST /api/projects/{project_id}/generate
Content-Type: application/json
X-Reqlica-Groq-Key: <optional per-request key>

{
  "requirements": "Add a typed GET /greeting endpoint with HTTP tests.",
  "model": "openai/gpt-oss-20b",
  "temperature": 0,
  "max_tokens": 8192
}
```

`requirements` is required; the other fields have the defaults above. Put a
per-request key in the header, not the JSON body or URL. It overrides the console's
environment key. Keys are never included in model messages, response payloads,
draft files, or browser storage. Server requests use a fixed Groq HTTPS origin,
ignore proxy environment variables, and do not follow redirects.

HTTP `201` returns `status: "draft_created"`, the inspected `draft`, `summary`,
`assumptions`, `output_warnings`, `generated_files`, proposed `endpoints`,
provider/model/token usage, `context_revision`, `checks: "not_run"`, and a security warning. The draft's
`validation`, `test`, and `approval` are all unset.

## Independent contract and environment checks

Repair requests group identical failures across timezone runs and omit repeated
framework stack frames, retaining project frame locations and final causes.
Complete source, original requirements and tests are still supplied; complete
diagnostics remain in saved checks. Provider HTTP 413 is surfaced as a request-size
or model/account token-allowance rejection, with guidance to lower output tokens
or use a larger allowance. No automatic retry or silent contract truncation occurs.

Generation and repair share domain-independent testing guidance: preserve each
operation's own constraints; cover valid boundaries and invalid neighbors,
business errors versus schema errors, state transitions, isolation, rollback,
retries, and timezone-independent time handling. These rules do not recognize
special route names such as `/auth/login`.

When **Run tests** has original requirements saved in `draft.generation`:

- A complete OpenAPI 3.0/3.1 JSON or YAML document (optionally fenced) is used as
  the independent contract, not the implementation's generated OpenAPI. Malformed
  recognizable OpenAPI fails the check. Prose or a document embedded in prose is
  reported as skipped, never silently converted into an invented contract.
- Simple JSON-body and query/path/header/cookie constraints are compared against
  the loaded app: narrower string/number/array bounds, excluded enum values,
  added required inputs, forbidden declared fields, and missing routes fail checks.
  This is a comparison of declared restrictions, **not a full schema-subset proof
  or automatic boundary fuzzing**. Composed input schemas and unsupported local
  constructs are reported as gaps; patterns, exclusive bounds, defaults and custom
  validators still need explicit behavioral tests.
- HTTP responses exercised through `fastapi.testclient.TestClient` are checked
  for declared statuses, JSON media types, and original response schemas (including
  referenced schemas, formats, and OpenAPI 3.0 nullable). Declared response headers
  are schema-checked when present; OpenAPI header declarations alone do not make
  their presence mandatory. Diagnostics name violated schema constraints without
  including response values such as tokens or personal data.
- The suite runs normally and then in fresh subprocesses under `UTC` and
  `Asia/Kathmandu`. Each run starts with an identical disposable database copy and
  fresh imports. The parent timezone, live data, source and original tests are not
  changed. Variant failures fail the overall result, even when the normal run
  passes. Variants cannot reduce passing coverage by skipping original tests.

`test.contract_checks` reports partial coverage, warnings and unexercised routes;
`test.environment_checks` summarizes timezone runs. Read these in the dashboard's
technical check details. Tests without saved requirements still run normally, with
a skipped-contract explanation; older/manual drafts can save their original
requirements through the existing confirmed repair workflow. No extra provider
call is made by the checks. Each subprocess keeps the existing 10-second timeout,
so generated-draft testing can take up to three subprocess budgets.

Independent violations appear in ordinary structured failures and feed the same
explicit **Repair with AI** workflow. A repair cannot change original requirements,
test source, route registrations or schema plans to suppress these failures.
Approval and activation remain manual.

**Coverage limits:** this does not infer account setup, ownership rules, expected
business statuses, concurrency guarantees, or arbitrary state machines from prose.
An allowed 401 instead of an allowed 200 is caught only if a workflow test asserts
the right outcome. Direct handler calls, AsyncClient tests, unexercised routes,
non-JSON bodies and unsupported references are not response-conformance evidence.
Only local JSON-pointer references are supported; external documents are never
fetched. Partial checks may pass alongside coverage warnings; this is not a claim
of complete OpenAPI conformance or production readiness. Python execution remains
trusted and unsandboxed, including the original tests' OS/network capabilities.

## Context and output

Context includes published Python modules/tests, registered routes, optional
`PROJECT_CONTEXT.md`, live SQLite **definitions** and relationships, and the actual
SDK conventions. Generation excludes previous chats; chat adds only the supplied
bounded history. Both exclude other drafts, application records,
non-Python source such as `.env`, and provider credentials. Python files/notes can
still contain sensitive information: review what you send to an external model.

Groq is asked for strict JSON containing a single `files` array (handlers and
tests together), endpoint definitions, a summary, and assumptions. Reqlica then
independently rejects ambiguous JSON keys, invalid output/schema, unsafe or
case-colliding paths, Python syntax errors, missing handlers/tests, and duplicate
or conflicting routes. If Groq repeats **top-level `files` or `endpoints` arrays**,
Reqlica combines all their items in order without dropping any, then validates
the entire proposal. This deterministic normalization adds an `output_warnings`
entry shown in the dashboard; it makes no second model call. Repeated file paths,
duplicate/conflicting routes, scalar metadata keys, and nested keys still fail
instead of guessing which value to keep. It preserves existing routes and writes
changes through the existing revision-aware draft operations. No generated Python
is imported.

The network request holds no project lock. Before saving, the service rechecks
the project directory, active version, published content revision, and available
database-schema fingerprint. Changed context is a conflict, not permission to
overwrite newer work. Each successful request creates its own new draft; it does
not guess which previous draft belongs to the task.

## Limits and errors

- At most 32,768 requirement characters, 50 published Python source files, and
  96 KiB of serialized model messages. Oversized context is rejected, not silently
  truncated. Large-project source selection is not implemented in this first path.
- At most 32,768 output tokens, 256 KiB of returned JSON, 20 generated files,
  64 KiB per file, and 50 proposed routes.
- Generated `.py` files must be canonical paths below `endpoints/`, `models/`, or
  `tests/`. The only additional generated file is the declarative
  `database/schema.json` plan. No raw manifest writes, file/route deletions,
  dependency installation, executable migrations, or destructive schema changes
  are supported. Live table creation requires separate review and confirmation.
- `404`: missing project. `409`: invalid/stale project context. `413`: oversized
  context. `422`: invalid request. `502`: rejected provider response, unfinished
  output, or invalid generated proposal. `503`: missing credentials/provider rate
  limit. `504`: provider timeout. No automatic provider retries occur.
- Provider/output failures save no draft. If a disk error occurs **during draft
  writes**, HTTP `500` includes the new `draft_id` for inspection/recovery. A partial
  draft is retained rather than hidden or treated as success; published code and
  existing drafts are not modified.
- Groq HTTP `400` errors are classified into safe, actionable explanations for
  generated-JSON/schema failures, context limits, invalid schemas, output token
  budgets, and model/settings incompatibility. Raw provider messages and
  `failed_generation` source are never returned. An unknown cause is reported as
  unknown, not assumed to be a credentials problem. For generated-JSON failures,
  simplify the endpoint request or try `openai/gpt-oss-120b`; there is no automatic
  fallback or second provider call.
- On an HTTP/UI timeout, generation may still complete. Refresh drafts before
  retrying to avoid duplicate requests/drafts and provider charges.

**Local trusted use only.** Management APIs remain unauthenticated. Groq hosts the
model; weights are not bundled with Reqlica. Generated Python and tests are not a
security sandbox, even when checks use disposable database copies. Do not run
generated code before reviewing it. Tests can fail despite valid structured
output—for example, direct calls to functions with FastAPI `Query()` defaults do
not behave like HTTP requests. Approval and activation are never automatic.

### Explicit response serialization

Prefer returning typed models/dicts so FastAPI performs response serialization.
For explicit status codes or error envelopes, project handlers can import
`APIJSONResponse` from `reqlica.runtime.sdk`. It accepts the same arguments as
Starlette's `JSONResponse` and encodes nested Pydantic models, datetime, UUID,
and other FastAPI-supported values before rendering. Unsupported values still
fail; the helper does not stringify arbitrary objects or replace schema checks.
For exact decimal strings, define the field/serializer in a response model.

Generation, repair, and validation reject direct raw `JSONResponse` calls with
Python-mode `model_dump()` or double-encoded `model_dump_json()` content. Use
`model_dump(mode="json")`, `jsonable_encoder`, or the SDK response helper.
This is a targeted static check, not full data-flow analysis: runtime HTTP tests
remain necessary for values passed through variables or custom response classes.

Repair context retains complete source/tests and confirmed answers, omits unused
clarification options, groups identical failure evidence with all affected test
names/environments, and excludes test-authoring instructions. Distinct causes
remain intact. These reductions do not guarantee compliance with every Groq
account's request/token limits; a provider rejection still leaves saved code
unchanged for that attempt.


### Predictable prompt and output contracts

New API requests and reset/new browser preferences default to temperature `0`.
Every provider call uses seed `42`; explicit temperature settings and existing
saved browser preferences remain honored. These are best-effort repeatability
controls, not a guarantee of identical source code or correct behavior. Groq can
change its backend; see its [sampling API contract](https://console.groq.com/docs/api-reference).
No additional provider calls or automatic retries are introduced.

Generation, endpoint editing, clarification and project questions assemble context
once and validate the final operation-specific messages. Clarification no longer
fails a size check against an unused, larger implementation prompt. Repair shares
implementation invariants but receives no test-authoring instructions.

Model payloads use compact UTF-8 JSON with sorted object keys. Source text, requirements,
conversation chronology and route registration order remain intact; no files or
contracts are silently truncated. Clarification decisions retain the question ID,
kind, question and confirmed answer, omitting unused suggested answers. Full
questions remain saved and participate in the existing stale-context fingerprint.
AI context skips the generated API-schema summary lookup because that summary is
not sent to the model; regular MCP/project context still includes it by default.
The 96 KiB limit counts compact UTF-8 serialized messages, not tokens or the full
provider request including its response schema. Account/model token limits can
still reject a request that fits this local byte budget.

The generation system prompt is 10,796 UTF-8 bytes, down from 12,900 (16.3%) in
this revision; project source size is unchanged. Requirements, explicit decisions,
current source and author notes have an explicit precedence policy. Missing or
conflicting essential decisions request clarification rather than silent defaults.
Generation and answer schemas expose their existing length/count limits to the
provider, instead of relying solely on rejection after generation. Local path,
syntax, dependency, route, source-reference and byte-limit checks remain in place.
All model JSON parsers reject nonstandard NaN/Infinity literals.

Offline regression tests cover final prompt boundaries, Unicode preservation,
stable map serialization without reordering routes, stale clarification decisions,
provider sampling settings and schema bounds. These checks establish local contract
behavior; they do not measure live-model semantic accuracy or identical-output rates.

## Resumable generation (dashboard default)

The dashboard now creates a durable generation job rather than requesting every
file in a single response. `PUT /api/projects/{project_id}/generation-jobs/{id}`
accepts `GenerationRequest`; the client supplies a 32-character hexadecimal ID.
Repeating that PUT with identical settings returns the same job. Credentials are
never saved: supply `X-Reqlica-Groq-Key` on each step for providers needing a key.

`POST /api/projects/{project_id}/generation-jobs/{id}/step` performs at most one
provider call. The first call produces a small outline containing paths, routes
and shared interfaces. Subsequent calls produce one file at a time. Parsed files
pass path/size and Python syntax or database-plan checks before checkpointing.
The complete proposal must pass existing import/name/route and schema validation
before any draft is created. Integration failures have a bounded assembly repair
that must preserve all planned files and routes. Generated code is not executed.

Each unit permits at most three calls, including attempts interrupted by a server
restart. With at most 20 files plus planning and assembly, the job permits at most
66 provider calls per initial job. Explicitly choosing Resume after exhaustion
grants at most three additional attempts for the unfinished unit; it never resets
completed files or past usage. Each call uses the saved token and response-wait limits;
step prompts are capped at 512 KiB. Network/provider failures pause the job;
format and source-validation failures may advance to another bounded attempt.
An exhausted job retains its checkpoints but cannot keep spending automatically.
`POST /api/projects/{project_id}/generation-jobs/{id}/resume` grants the bounded
additional batch only when exhausted; repeated resume requests while ready do not
grant extra attempts.
A successful resume skips completed files. A changed project or response contract
invalidates the job instead of applying stale output. A draft-save interruption
requires inspection and cannot silently create another draft on resume.

`GET /api/projects/{project_id}/generation-jobs` lists recent jobs, and
`GET /api/projects/{project_id}/generation-jobs/{id}` reads one. Saved generations
and Resume controls appear in the dashboard after reopening a project. Progress
is server-side, so browser refreshes do not discard checkpoints. Closing the tab
stops dispatching new steps; an already-running step may still finish.

Checkpoints live under `.reqlica/generation-jobs/`, outside draft/code trees.
They contain requirements, model settings, generated source and the last failed
response for correction, but no API-key headers. Files are private and atomically
replaced. The list/inspect APIs expose progress and bounded diagnostics, not raw
source or failed output. There is no automatic checkpoint expiry. Remove an old
job's JSON file only when it is idle if you no longer need recovery.

Strict JSON schema is requested on direct APIs without a model-name regex.
OpenRouter uses the strongest advertised format: strict schema, JSON mode, or
text with schema instructions. It requires routing support for parameters actually
sent. Models without strict-schema support remain usable, but their formatting
is not provider-enforced; local validation and bounded corrections remain mandatory. Legacy `/generate` and endpoint editing retain their one-proposal API,
with one correction attempt for either JSON syntax or response-schema errors.

### Live reliability evaluation

Unit tests exercise failure handling with mocked provider responses. To measure
actual model reliability, use a saved specification and an isolated output folder:

```sh
uv run python -m reqlica.generation.evaluate \
  --requirements /path/to/social-media.yaml \
  --output-dir /tmp/reqlica-social-evaluation \
  --runs 3 --confirm-provider-spend
```

This uses Groq's configured environment key by default. For another provider,
pass `--provider`, `--model`, and optionally `--base-url`, with its credential in
`REQLICA_EVAL_API_KEY`. The benchmark creates isolated projects, retains checkpoints
and writes `metrics.json` with schema failures, recovery outcomes, calls, known
usage and latency. It uses real provider quota. It never runs generated code;
static success does not establish correctness of authentication or business rules.
