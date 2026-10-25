# AI usage disclosure

## AI in the application

Reqlica uses AI to transform requirements, API specifications, project source,
and check feedback into Python handlers, tests, route definitions, and synthetic
records. These outputs are consumed programmatically to build stateful mock APIs
and validate datasets.

The primary consumption point is **`step_run()` in
[`backend/reqlica/generation/agent.py`](../backend/reqlica/generation/agent.py)**.
It receives the model response from `provider.complete()`, validates it with
`_tool_call_issues()`, and queues `message.get("tool_calls", [])`. On subsequent
steps, **`_execute_tool()` in the same file** parses the JSON arguments, checks
scope and permissions through `_arguments()`, and dispatches allowed actions
through `session.call_tool()`.

The resulting actions have concrete effects:

- `write_draft_file()` and `upsert_endpoint()` in
  [`backend/reqlica/mcp/server.py`](../backend/reqlica/mcp/server.py) save generated
  source/tests and register draft routes.
- `preview_mock_data()` in
  [`backend/reqlica/mcp/data.py`](../backend/reqlica/mcp/data.py) parses generated
  JSON and validates records through `validate_data()` or `validate_related_data()`.
- After checks, human approval, and activation, `build_app()` in
  [`backend/reqlica/runtime/worker.py`](../backend/reqlica/runtime/worker.py)
  loads published handlers and registers executable FastAPI routes.

This is the basis for the “Build with AI” requirement: model output becomes
executable API logic and validated data. Published mock requests run Python
without a model call. Built-in Project AI cannot approve or publish code or
insert live records. See [architecture](architecture.md) and
[limitations](limitations.md) for workflow and execution boundaries.

## AI assistance during development

I used AI heavily for frontend development because I lack frontend expertise,
and for documentation preparation. All tests were automated using OpenCode and
GPT-6.1. AI provided minor assistance with backend development and technical
queries. I checked all AI-generated content before approving it.

[Documentation index](README.md) · [Project overview](../README.md)
