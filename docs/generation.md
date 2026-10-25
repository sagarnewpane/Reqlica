# Project AI

The dashboard has one MCP-backed conversation for project questions, API creation,
attached or pasted specifications, endpoint changes, repairs, checks, and synthetic
data previews. There is no separate Generate draft / Ask / Repair AI pipeline.
See the [dashboard walkthrough](mcp.md#project-ai-inside-the-dashboard).

## Use the conversation

1. Configure a tool-capable Groq, OpenAI, or OpenAI-compatible model in Settings.
2. Open Project AI and choose context and permissions in Conversation setup.
   Questions should not edit code; disable draft edits to enforce that boundary.
   Validation/tests/disposable requests require separate execution permission.
3. Send a message or attach/paste a specification. Clarifications and follow-ups
   use the same composer, selected provider, assigned draft, and remaining budget.
4. Inspect tool actions and actual revision-linked check results. Review source
   files in Drafts & checks. Manual checks, approval, activation, live database
   setup, environment values, and live data insertion stay under human control.

Each browser step performs one model request or one MCP call. The AI chooses its
next tool call; server permissions, one-project/one-draft scope, revisions, and
budgets constrain it. Tool failures return feedback to the model. Check execution
runs trusted Python with OS permissions on disposable data, not in a sandbox.

The model is instructed to read tool descriptions, prerequisites and argument schemas
before calling, reuse saved evidence, and wait for reads/checks before dependent calls.
Tool argument, source and check failures return compact repair feedback with locations
and a next step. Recoverable failures keep the conversation running without setting a
conversation error. Remaining calls in a failed batch are skipped with matching tool
receipts; earlier successful changes stay saved. Identical failed calls at the same
draft revision are blocked before execution. Three consecutive tool failures stop
automatic repair; successful calls reset that counter. Unresolved failures prevent a
success answer. Permission, transport and revision conflicts still require attention
when safe automatic recovery is unavailable.

Context compaction preserves user requirements, current source evidence and structured
repair diagnostics, including source locations, dependency issues and recovery status.
Complete history remains in the checkpoint. Optional tool groups limit catalog size;
argument descriptions and fallback tool documentation retain their full prerequisites.

## Console API

All project paths start with `/api/projects/{project_id}`.

| Method/path | Purpose |
| --- | --- |
| `PUT /agent-runs/{run_id}` | Create an idempotent conversation checkpoint. |
| `GET /agent-runs` | List recent saved conversations. |
| `GET /agent-runs/{run_id}` | Read a saved conversation without advancing it. |
| `POST /agent-runs/{run_id}/step` | Advance one model request or one MCP call. |
| `POST /agent-runs/{run_id}/replies` | Append an idempotent follow-up to a completed turn. |
| `POST /agent-runs/{run_id}/reasoning-default` | Reset a rejected saved reasoning choice to Provider default without a model call. |
| `GET /activity/{activity_id}` | Read content-free activity stages for a request. |

Creation accepts `message`, optional `draft_id` plus `expected_revision`,
`allow_edits` (default true), `allow_execution` (default false), and provider settings.
Legacy projects must disable edits until converted to versioned storage.
Messages plus attached text fit within 32,768 characters.

Provider settings are `provider`, `base_url`, `model`, `reasoning_mode`,
`temperature`, `max_tokens` (256–65,536; default 8,192), and `timeout_seconds`
(30–600; default 300). The provider and model may impose lower token limits.
Settings apply to new conversations; saved conversations retain their output budget.
Keys are supplied in `X-Reqlica-Groq-Key`, never request bodies/checkpoints.
Only Groq profiles can fall back to server `GROQ_API_KEY` / `GROQ_API`; custom
providers receive only their explicitly supplied credential.
`POST /api/ai/reasoning-options` returns supported reasoning choices.
Model identifiers are required; Reqlica has no default model or model-name allowlist.

Settings → Add/Edit provider → **Fetch models** calls `POST /api/ai/models` with
`provider` and `base_url`, plus an optional `X-Reqlica-Provider-Key` header.
OpenRouter uses its public `/models` catalog without a key. Groq uses its
`/models` endpoint with the browser key or server Groq key. Ollama uses
`/api/tags` to list installed models; this does not pull or load models.
OpenAI and compatible providers can also expose `/models`. Unsupported endpoints,
empty catalogs, and failed requests allow manual model entry. Ollama profiles run
Project AI through `/v1/chat/completions` using the selected model and tools. Local
Ollama needs no API key; remote servers require an explicit key. Models must support
tool calls. Anthropic profiles remain preview-only.

Discovery sends no project content and does not run inference. Credentials are
excluded from response bodies and caches; only the public OpenRouter catalog is
cached briefly. Refresh requests reload it. Lists do not prove chat/tool access.
Reasoning controls use reported metadata, never model-name guesses. Where effort
levels are absent (including Groq's current catalog), only Provider default is
shown. Default omits the reasoning override for every provider. Existing saved
explicit choices may need resetting to Provider default when no capability
metadata verifies them.

The model catalog is discovery metadata, not an availability allowlist. A manually
entered ID is sent unchanged even when absent from the public catalog, and a
catalog outage does not block default-reasoning inference. Ordinary OpenRouter
tool requests use its default provider routing. Explicit reasoning overrides
still require parameter support so an Off/effort choice is not silently ignored.
After an OpenRouter 404, Reqlica checks its public endpoint metadata to distinguish
an empty provider list from an unknown routing failure. This sends no credentials
or project content and does not retry inference or substitute another model.

Provider 413 errors retain recognized token-quota metrics and numeric Limit /
Requested values in technical details. Raw provider messages, organization IDs,
credentials, and echoed source are excluded. Message and tool-definition byte
counts are reported separately; these are not token counts. A model's context
window and an account's token allowance are separate constraints. Existing
conversations retain their saved output budget when Settings is edited.

Replies require `message`, a unique `reply_id`, `expected_turn`, and
`expected_revision`. They cannot change provider, scope, permissions, or reset
budgets. Changed source stops continuation for review. Publishing the chat's exact
saved revision allows the next reply in the same conversation; further edits use
a fresh draft based on the published version. The original draft stays intact.
New chat can explicitly leave an unconfirmed send once its request has ended;
the previous server checkpoint stays in history and is not replayed in the new chat.

Conversations retain their saved reasoning setting when a provider profile is edited.
The setup summary shows the conversation’s actual choice. For an unsupported choice,
use **Use Provider default**, then Resume. The reset requires `expected_revision` and
`expected_reasoning_mode`, preserves history/permissions/budgets, and cannot replay
pending tools or recover unrelated failures. New conversations check non-default
profile choices and disclose a switch to Provider default before sending.

## Recovery and limits

Conversations persist privately in `<project>/.reqlica/agent-runs/` across refreshes
and server restarts. Each conversation allows 24 model requests and 64 tool calls,
with a 512 KiB prompt bound. Source files fit 64 KiB. Provider keys are excluded
from checkpoints; messages, source, tool results and data previews are retained.
Project AI sends short MCP summaries and omits repeated schema titles. Project
and draft IDs appear once in assigned scope and are injected into tool calls by
the server instead of being repeated in every model-facing parameter schema.
Tool names, business parameters/help, defaults/examples, validation constraints,
explicit revision checks and permission enforcement remain intact. The external
MCP catalog keeps its full schemas and documentation.

Tool-result JSON is minified only in the model-facing copy. All parsed values,
source strings, check results, message order and tool-call IDs are preserved;
user/assistant text and saved checkpoints are not rewritten or summarized.
Invalid/ambiguous JSON stays unchanged. These reductions apply when resuming
existing conversations as well as new ones. They reduce overhead but do not
guarantee that growing history fits an account's token allowance.

Pause stops future steps after the current request finishes. Closing the tab also
stops further advancement, but an in-flight request may finish. Failed provider
requests consume budget and require explicit resume. Uncertain interrupted tool
calls are never replayed automatically; inspect saved work before continuing.
There is no fallback to the retired structured-proposal generator when tool
support is unavailable.

## Removed APIs and older projects

The separate `/generate`, `/clarify`, `/ask`, `/edit-endpoint`, `/endpoint-source`,
`/generation-jobs` family, `/drafts/{draft_id}/repair`, and data-generation endpoints
have been removed. The old proposal schemas, generation/review prompts,
plan/file sequencer, correction retries, repair loop, and live-generation benchmark
were removed with them. External clients using those routes must use agent-runs
or connect directly to MCP. Normal route discovery returns 404 for retired APIs.

Existing drafts, source, checks, approvals, published versions and records are
kept. Continue a saved draft through Project AI or edit/check it manually.
Old `.reqlica/generation-jobs/` and repair-backup files are left on disk untouched;
the retired jobs cannot resume. Earlier browser chat remains viewable/clearable
as a read-only archive and is never sent as new conversation history.
