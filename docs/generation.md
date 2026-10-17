# Single-turn endpoint generation

Reqlica can make one Groq request containing requirements and current published
project context, then save a new unpublished draft. It uses the same
`get_project_context()` helper as MCP. There is no conversation history, remote
MCP access, tool-execution loop, automatic retry, or automatic repair.

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
5. **Preview generation brief** stays local. **Generate draft with Groq** asks
   confirmation before sharing requirements and project source/definitions.
6. Review the returned summary, assumptions, and draft files. Only after review,
   explicitly run **Validate**, **Run tests**, and disposable requests. Fix failed
   code/tests manually; success is not assumed from a valid JSON response.
7. Approval remains CLI-only and activation remains explicit. Nothing is live
   merely because generation succeeded.

Model, temperature, and maximum output tokens are used for real generation.
This initial generator always requests typed contracts, tests, and assumptions;
the other preference checkboxes still apply only to UI previews. Other provider
types, the separate endpoint-edit brief, and sample-data fixtures remain previews.

## Console API

### Ask about the project (read-only)

In the same **Generate API** screen, switch to **Ask about project**. For example:
"How is POST /books designed? Explain its inputs, rules, and data relationships."
Select a Groq profile and confirm sharing the current project context. The answer
shows source references, limitations, and the published snapshot revision. Switch
back to **Generate draft** to request implementation; Ask mode cannot create drafts
or execute changes, even if the question asks it to.

```http
POST /api/projects/{project_id}/ask
Content-Type: application/json
X-Reqlica-Groq-Key: <optional per-request key>

{
  "question": "How is GET /hello implemented and tested?",
  "model": "openai/gpt-oss-20b",
  "temperature": 0.2,
  "max_tokens": 4096
}
```

HTTP `200` returns `status: "answered"`, `answer` (plain text), `sources`,
`limitations`, `context_revision`, provider/model/usage, and a read-only warning.
Source references must identify supplied Python/tests, available `manifest.json`
or `PROJECT_CONTEXT.md`, `database_definitions`, or `sdk`; unknown references are
rejected. References are checked for existence, not proof that the explanation is
correct. Answers do not establish runtime health or successful tests, and inferred
design rationale may be wrong. They describe the snapshot read before the model
request, not changes made while the provider was responding.

Questions use the same context helper, privacy boundaries, context/provider limits,
and credentials as generation. Questions are limited to 16,384 characters; answer
text to 32,768 characters, with up to 50 references and 20 limitations. No history
is sent. Only the current question and latest answer are held in per-project page
memory; they are not saved to browser storage or project files. The dashboard uses
your saved model/temperature/token settings; the API's default answer budget is
4096 tokens. It makes one provider call, without retry, repair, or fallback.

### Generate an endpoint draft

```http
POST /api/projects/{project_id}/generate
Content-Type: application/json
X-Reqlica-Groq-Key: <optional per-request key>

{
  "requirements": "Add a typed GET /greeting endpoint with HTTP tests.",
  "model": "openai/gpt-oss-20b",
  "temperature": 0.2,
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

## Context and output

Context includes published Python modules/tests, registered routes, optional
`PROJECT_CONTEXT.md`, live SQLite **definitions** and relationships, and the actual
SDK conventions. It excludes previous chats, other drafts, application records,
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
  `tests/`. No raw manifest writes, file/route deletions, dependency installation,
  database initialization, or migrations are supported.
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
