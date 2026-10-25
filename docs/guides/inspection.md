# Request history and errors

Published gateway responses carry `X-Reqlica-Request-ID`. Use it to correlate
request events with handler errors. The gateway replaces caller-supplied IDs.

## Read history

- `GET /api/projects/{project_id}/logs`: newest first, with `limit` (1–100),
  `before_id`, `request_id`, and `kind` filters.
- `GET /api/projects/{project_id}/logs/{event_id}`: one retained event.
- MCP: `read_project_logs`.

Kinds are `request`, `runtime_error`, `startup_error`, `stdout`, and `stderr`.
List responses include `events` and `next_before_id`. Requests record method,
path, status, duration, and ID. Errors include exception class and project frames;
startup errors have no request ID. Events do not prove current health or test coverage.

## Capture and retention

By default, bodies, headers, queries, exception messages, and locals are excluded.
GET/PUT `/api/projects/{project_id}/logs/settings` controls `capture_payloads`,
`capture_output`, and `max_body_bytes` (128–2,048; default 1,024). Writes pin
`expected_revision`. Capture updates apply immediately.

Sensitive field names and configured environment values are redacted on a
best-effort basis. Other secrets may remain, especially in stdout/stderr. Enable
capture only with suitable test data. Disabling it does not remove retained events.

`.reqlica/request-history.sqlite` retains 1,000 combined events, up to 8 KiB each.
Logs survive restarts. Recording can fail under crashes, contention, or full disks;
it is not an audit log. Management/demo/docs traffic, intercepted preflights, and
disposable checks/previews are excluded.

[Documentation](../README.md)
