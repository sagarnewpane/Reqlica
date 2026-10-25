# AI usage disclosure

## AI in Reqlica

AI turns requirements and specifications into Python handlers, tests, routes,
and synthetic records. The backend saves and checks these outputs; they become
executable API logic and data rather than only a displayed answer.

The main consumption point is **`step_run()` in
[backend/reqlica/generation/agent.py](../backend/reqlica/generation/agent.py)**.
It reads `tool_calls` from `provider.complete()`. `_execute_tool()` in the same
file parses and checks arguments, then dispatches actions through MCP.

`write_draft_file()` and `upsert_endpoint()` in
[mcp/server.py](../backend/reqlica/mcp/server.py) save code and register routes.
`preview_mock_data()` in [mcp/data.py](../backend/reqlica/mcp/data.py) parses and
validates generated records. After human approval and activation, `build_app()`
in [runtime/worker.py](../backend/reqlica/runtime/worker.py) loads the handlers.
Published requests do not call a model.

## Development assistance

I used AI heavily for frontend development because I lack frontend expertise,
and for documentation preparation. All tests were automated using OpenCode and
GPT-6.1. AI provided minor backend and technical-query assistance.
I checked all AI-generated content before approving it.

[Documentation](README.md)
