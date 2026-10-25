# Project AI

Project chat handles API creation, changes, questions, tests, repairs, and sample
data. It saves the conversation so you can return to it later.

## Workflow

1. Configure a tool-capable model and credentials in **Settings**.
2. Open **Project chat → Model & permissions**. Choose context and allow draft
   edits or Python execution as needed; these are separate permissions.
3. Describe the task or attach an OpenAPI specification.
4. Review tool results and saved files. Run validation and tests on the final draft.
5. Approve and activate the checked revision yourself.

Project AI cannot publish, install packages, set secrets, or insert live records.
Checks execute Python on disposable copies, with the local user's OS access.

## Data preparation and completion

Ask for a complete dataset, including counts, tables, and relationships.
`get_mock_data_context` reads live definitions and contracts without existing rows.
`preview_mock_data` validates generated records. A passed preview stops the turn
for review. Use **Review mock data** to edit and revalidate in Sample data;
insertion needs separate confirmation. See [mock data](mock-data.md).

## Providers and credentials

Built-in providers are Groq, OpenAI, OpenAI-compatible APIs, and Ollama.
Models must support tool calls; Anthropic profiles are not accepted by this API.
External agents can use the [MCP server](mcp.md) with their own model setup.

Provider keys are stored as private local plaintext under
`$REQLICA_HOME/provider-keys/application.json`, outside projects and conversations.
**Show key** explicitly reveals a saved value. Ollama needs no key.
Groq can fall back to `GROQ_API_KEY` or `GROQ_API`.

## Budgets and recovery

| Limit                        | Default |
| ---------------------------- | ------- |
| Model calls per conversation | 24      |
| Model calls per turn         | 16      |
| Tool calls                   | 64      |
| Provider-reported tokens     | 100,000 |
| Serialized model context     | 512 KiB |

Use **Conversation budgets** to raise limits without resetting usage.
Model/tool limits allow 1–1,000; token limits allow 1,000–1,000,000.
Per-turn model calls cannot exceed the conversation limit. Provider limits apply too.

One step performs one model request or tool call. Calls run sequentially.
Three consecutive tool failures stop automatic repair. Failed calls consume budget.
After a timeout, inspect saved work before retrying an uncertain write.
Pause or closing the tab stops future steps after the in-flight request finishes.

Checkpoints in `<project>/.reqlica/agent-runs/` retain prompts, source, tool data,
previews, and usage. Provider keys are excluded, but user-entered secrets can remain.

## Console API

Project routes start with `/api/projects/{project_id}`:

| Method/path                                   | Action                                |
| --------------------------------------------- | ------------------------------------- |
| `PUT /agent-runs/{run_id}`                    | Create a checkpoint.                  |
| `GET /agent-runs`                             | List conversations.                   |
| `GET /agent-runs/{run_id}`                    | Inspect a conversation.               |
| `GET /agent-runs/{run_id}/context`            | Inspect model input and tool schemas. |
| `POST /agent-runs/{run_id}/step`              | Advance one step.                     |
| `POST /agent-runs/{run_id}/replies`           | Add a follow-up.                      |
| `POST /agent-runs/{run_id}/limits`            | Update budgets.                       |
| `POST /agent-runs/{run_id}/reasoning`         | Change eligible idle-chat reasoning.  |
| `POST /agent-runs/{run_id}/reasoning-default` | Reset a rejected reasoning choice.    |
| `POST /agent-runs/{run_id}/recover`           | Inspect interrupted work.             |
| `POST /specifications/preview`                | Inspect a specification.              |

Creation takes `message`, model settings, optional specification/draft selection,
`allow_edits`, and `allow_execution`. Replies pin the turn and revision and use a
unique `reply_id`. Full request schemas are in the console's `/docs`.
The old one-shot generation endpoints are retired.

[Documentation](../README.md)
