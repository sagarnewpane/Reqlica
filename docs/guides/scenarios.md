# Gateway scenarios

Configure route delays, synthetic failures and empty JSON lists through the
console API. Settings are per project, disabled by default, and apply immediately
to published gateway requests. They are outside draft code, checks and approval.
Worker startup/imports may occur before a scenario response; the handler request
is not forwarded for synthetic failures or empty responses.

## Management API

`GET /api/projects/{project_id}/scenarios` returns `revision` and `rules`.
`PUT` at the same path replaces all rules and requires `expected_revision` from
GET. Stale configuration writes return 409. Successful reads/writes use
`Cache-Control: no-store`. Invalid requests return 422. Remove all rules with
`rules: []`. No restart or code activation is required.

Example PUT body:

```json
{
  "expected_revision": "<revision from GET>",
  "rules": [
    {
      "method": "GET",
      "path": "/books/{book_id}",
      "delay_ms": 250,
      "response": "failure",
      "status_code": 503,
      "body": { "error": "temporarily_unavailable" },
      "failure_count": 3
    }
  ]
}
```

Use the exact registered method and route pattern, including placeholder names;
`/books/{book_id}` covers concrete requests such as `/books/123`. Query parameters
do not affect matching. Matching follows the worker's first registered route for
that method; a dynamic route's scenario does not override an earlier static route.
Rules for absent routes are dormant until that exact route exists. Settings do
not manufacture routes or override normal unknown-route/method responses.

Each rule supports:

- `delay_ms`: 0–30,000 milliseconds, default 0. Async delay before the synthetic
  response or handler forwarding. Worker startup and handler execution time are
  additional. Delays continue after a finite failure count is exhausted.
- `response`: `passthrough` (default), `failure`, or `empty`.
- `failure`: return `status_code` (400–599, default 503) and JSON `body` (default
  `{"detail":"simulated_failure"}`; maximum 16 KiB). A JSON null body is supported.
- `empty`: return HTTP 200 with `[]` without invoking the handler.
- `failure_count`: positive integer up to 1,000,000, only for `failure`. Omit/null
  to fail every matching request. Once exhausted, forward subsequent requests.
- `rate_limit`: optional shared or per-test-user quota, checked before delays and
  failure reservations; see [rate limits](rate-limits.md).

At most 100 unique method/pattern rules and 2 MiB of stored settings are allowed.
Finite rules expose `remaining_failures` in GET responses. Full replacement resets
all configured counters. Counter consumption does not change the configuration
revision; replacing settings always creates a new revision, even for identical rules.
Read current counters again after requests when displaying them.

## Runtime behavior

Authentication runs first. Rejected credentials do not consume failures or incur
scenario delays. Scenario responses have `Cache-Control: no-store` and
`X-Reqlica-Scenario: failure` or `empty`. HEAD synthetic responses have no body.
Real responses after passthrough retain their handler headers/status.

Failure reservations are serialized across threads/processes before delaying.
A request cancelled after reservation still consumes one failure. Cancellation
releases its worker lease; delays hold no settings locks. Configuration changes
only affect subsequent reservations; in-flight requests retain their chosen action.

Settings and remaining counts persist atomically in `.reqlica/scenarios.json`,
with an independent file lock to avoid blocking activation while it drains requests.
Counts survive worker/gateway restart and code publication. Invalid settings cause
503 for matched published routes rather than executing them with guessed settings.

Demo, management and docs URLs, draft previews and draft test calls are outside
this policy. Auth and lifecycle failures (including stopped/activating workers)
take priority over scenarios. External MCP clients can inspect and replace policies
or reset counters through [confirmed operations tools](mcp.md#project-operations).
Built-in Project AI can inspect policies but cannot change them.
Rate limits can use caller-selected test-user labels. Explicit [scenario counter
reset](reset.md) rearms rules without replacing them; data restore is separate.

[Documentation index](../README.md) · [Project overview](../../README.md)
