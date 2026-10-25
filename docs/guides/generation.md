# Project AI

Project chat provides one saved MCP-backed conversation for questions,
specifications, API creation, changes, tests, repairs, and synthetic data previews.
See [MCP](mcp.md) for external clients and the tool catalog, and
[AI usage disclosure](../ai-usage.md) for exact code paths consuming model output.

## Workflow

1. Configure a tool-capable Groq, OpenAI, OpenAI-compatible, or Ollama model in
   Settings. Save a provider key if required; local Ollama needs none.
2. Open Project chat and inspect **Model & permissions**. Select published context
   or an existing editable draft. Draft editing and Python execution are separate
   permissions; disable edits for enforced read-only questions or data preparation.
3. Describe the task or attach a specification. Sending starts the model request;
   opening chat or following a prepared repair link does not.
4. Inspect tool receipts, saved source/routes, and revision-linked check results.
   The model can correct recoverable tool/check failures in the same draft.
5. Review the final source and run required validation and behavior tests. Human
   approval, activation, live database changes, and secret configuration remain
   separate actions.

One step performs one model request or one MCP call. Multiple calls from a model
response execute sequentially with current revisions, not concurrently. Scope,
permissions, argument checks, and budgets are enforced by the server. Checks run
trusted Python with OS access on disposable code/data; they are not sandboxed.

## Data preparation and completion

Request one complete dataset, including counts, target tables, and relationships.
SQLite/related preparation reads live definitions and published application
contracts through `get_mock_data_context`; it does not read existing rows.
Related new records can use `{"$ref":"users.0.id"}` instead of guessed generated IDs.
JSON fixtures do not need a code draft or database context.

A passed `preview_mock_data` result automatically ends the turn, displays the
records, and skips remaining queued tools. Use **Review mock data** to open
Sample data, edit if needed, validate again, and separately confirm insertion.
The AI waits for an explicit reply before continuing. Failed previews remain
feedback for bounded correction and cannot be inserted.

`finish_task` also ends tasks with no code draft. Repeated attempts to finish a
code task without resolving feedback stop after two unsuccessful attempts.
A preview is neither code-test coverage nor human approval.

## Console API

Project paths below start with `/api/projects/{project_id}`. The dashboard uses
same-origin `/api/ui/` adapters; direct API clients use these management routes.

| Method/path                                   | Purpose                                                                  |
| --------------------------------------------- | ------------------------------------------------------------------------ |
| `PUT /agent-runs/{run_id}`                    | Create an idempotent checkpoint.                                         |
| `GET /agent-runs`                             | List recent conversations.                                               |
| `GET /agent-runs/{run_id}`                    | Inspect without advancing.                                               |
| `GET /agent-runs/{run_id}/context`            | Inspect model messages and tool schemas.                                 |
| `POST /agent-runs/{run_id}/step`              | Advance one model request or tool call.                                  |
| `POST /agent-runs/{run_id}/replies`           | Append an idempotent explicit follow-up.                                 |
| `POST /agent-runs/{run_id}/limits`            | Change saved budgets using current revision and expected old limits.     |
| `POST /agent-runs/{run_id}/reasoning`         | Change reasoning while eligible and idle.                                |
| `POST /agent-runs/{run_id}/reasoning-default` | Reset a rejected reasoning choice without inference.                     |
| `POST /agent-runs/{run_id}/recover`           | Inspect/recover an interrupted reservation without blindly replaying it. |
| `POST /specifications/preview`                | Inspect a selected specification before generation.                      |
| `GET /activity/{activity_id}`                 | Inspect content-free request stages.                                     |

Creation accepts `message`, optional `specification`, optional `draft_id` with
`expected_revision`, `allow_edits` (true), `allow_execution` (false), model
settings, and budgets. Legacy projects require read-only mode until converted.
Messages fit within 32,768 characters. Specification uploads fit 128 KiB, and
selected specification context must fit 24 KiB; narrow operations when necessary.

Replies require `message`, a unique `reply_id`, `expected_turn`, and
`expected_revision`. They preserve provider, permissions, and recorded usage.
Outside code changes can block continuation. Publishing the chat's exact saved
revision allows a follow-up to use a fresh draft based on that published code.

## Providers and credentials

Model settings include `provider`, `base_url`, `model`, `reasoning_mode`,
`temperature` (0–2), `max_tokens` (256–65,536; default 8,192), and
`timeout_seconds` (30–600; default 300). Provider/model limits may be lower.
Anthropic profiles are not accepted by the current backend conversation API.

Keys saved in Settings are app-wide backend secrets scoped to provider and URL.
They are outside project source and checkpoints, in
`$REQLICA_HOME/provider-keys/application.json`. These are local plaintext files
protected by owner-only permissions, not encryption. Explicit **Show key** returns
a saved value to the page. Groq alone can fall back to `GROQ_API_KEY` or `GROQ_API`;
custom destinations never receive that fallback.

Direct step clients may supply a request credential through the legacy-named
`X-Reqlica-Groq-Key` header. Discovery uses `POST /api/ai/models` with provider/base
URL and an optional `X-Reqlica-Provider-Key`; otherwise it uses the saved app key.
`POST /api/ai/reasoning-options` reports supported reasoning choices.

Discovery sends no project content and does not run inference or pull Ollama
models. Catalog membership does not prove account access or usable tool behavior;
manual model entry is available. Reasoning settings use capability metadata.
**Provider default** omits an explicit override. Saved chats keep their model and
reasoning choice when a profile changes; an eligible idle chat can change reasoning
explicitly. Provider errors expose bounded diagnostics rather than raw echoed input.

## Budgets and recovery

| Limit                          | Default | Configurable range                         |
| ------------------------------ | ------- | ------------------------------------------ |
| Model calls per conversation   | 24      | 1–1,000                                    |
| Model calls per turn           | 16      | 1–1,000, no higher than conversation limit |
| Tool calls                     | 64      | 1–1,000                                    |
| Provider-reported total tokens | 100,000 | 1,000–1,000,000                            |
| Serialized model context       | 512 KiB | Fixed server bound                         |

Use **Conversation budgets → Update this chat's budgets** to change an existing
chat. Updates preserve usage and pending work and do not execute anything.
Provider quotas and model context windows apply separately; byte counts are not
token counts. Source writes fit 64 KiB; provider responses are bounded to 256 KiB.

Tool errors return structured correction feedback. Remaining dependent calls in a
failed batch are skipped; earlier saves remain. An identical failed call at the
same revision is blocked before execution. Three consecutive tool failures stop
automatic repair; successful calls reset that counter. Unresolved failures cannot
be presented as a successful code completion.

Private checkpoints in `<project>/.reqlica/agent-runs/` retain requests, source,
transcripts, previews, usage, and pending work across restarts. Context compaction
selects bounded model-facing evidence while preserving full checkpoint history.
Provider keys are excluded, but user-entered sensitive text can remain.

Pause or closing the tab stops future advancement after any in-flight request.
Provider failures consume budget and may require explicit resume. After an
uncertain write, inspect saved work; an interrupted action is not automatically
replayed. Scope conflicts or unresolved recovery require review.

## Compatibility

The retired `/generate`, `/clarify`, `/ask`, `/edit-endpoint`, `/endpoint-source`,
`/generation-jobs`, `/drafts/{draft_id}/repair`, and data-generation endpoints are
not the current API. Use saved conversations or MCP. Existing source, drafts,
versions, and data remain usable; retired generation jobs cannot resume.

[Documentation index](../README.md) · [Project overview](../../README.md)
