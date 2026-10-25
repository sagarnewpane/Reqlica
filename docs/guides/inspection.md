# Request history and runtime errors

Published gateway traffic produces persistent project-local inspection events.
Each response, including gateway rejections, returns a server-generated
`X-Reqlica-Request-ID`. Caller-supplied values are replaced before forwarding to
workers. Use this ID to correlate a request event with a handler exception.

## Console API

- `GET /api/projects/{project_id}/logs`: newest events first. Optional `limit`
  (1–100, default 50), `before_id` (exclusive positive event ID), `kind`
  (`request`, `runtime_error`, `startup_error`, `stdout`, `stderr`), and `request_id` (32 hex characters).
- `GET /api/projects/{project_id}/logs/{event_id}`: one retained event; 404 if absent
  or already removed by retention.

List responses contain `project_id`, `events` and `next_before_id`. Supply that
cursor with the same filters for older pages. Event IDs increase within each
project. Successful reads use `Cache-Control: no-store`. Missing projects return
404, invalid query parameters 422, and unreadable/corrupt history 409. Reads do
not start workers, execute handlers or create drafts.

`read_project_logs` exposes the same paged/filterable evidence through read-only
MCP and Project AI, without execution permission. History reflects saved events,
not current worker health or proof of test coverage.

## Captured evidence

Request events contain UTC timestamp, request ID, HTTP method, gateway-relative
path (up to 1,024 characters), status, elapsed milliseconds including worker
acquisition/delays, outcome and response-body byte count where available. Synthetic
responses, authentication failures, quotas and gateway errors are included.
Cancellation is recorded as inspection status 499 when possible; this is not a
response delivered to the client. Unknown/unregistered projects have no history.

Unhandled handler exceptions produce `runtime_error` events with request ID,
exception class and up to 12 project frame locations (relative file, function,
line). Worker startup/process exceptions produce `startup_error` events; startup
occurs before request forwarding, so these have no request ID. Expected HTTP errors
and application validation failures appear as request statuses rather than Python
exception events.

By default, no request/response bodies, query strings, headers, cookies, exception messages,
source lines or local variables are stored. Paths can still contain user-provided
identifiers. Optional bounded payload and stdout/stderr capture is available below; streaming
log subscriptions and full payload replay are not implemented.

## Storage and limits

`.reqlica/request-history.sqlite` retains the latest 1,000 combined request/error
events, with an 8 KiB cap per event. SQLite transactions serialize writers across
gateway/worker processes; the database uses private creation permissions and
reuses pages after retention deletes. History persists across restart/publication
and is separate from application data, scenario counters and draft revisions.

Recording is best effort with a short database lock timeout. Full disks, corrupt
history, cancellation, process crashes or contention can omit events; inspection
failure does not change an API response. The history database is not an audit log.
Demo/management/docs requests and disposable draft checks/previews are not captured.
Browser preflights intercepted by enabled project CORS are also excluded.

## Optional capture

GET/PUT `/api/projects/{project_id}/logs/settings` manages `expected_revision`,
`capture_payloads` (default false), `capture_output` (false), and `max_body_bytes`
(default 1,024; 128–2,048). Settings changes are immediate for running workers.
Request bodies are captured only when normal forwarding already read them;
rejected requests are not read solely for logging. Response previews include byte
counts and truncation flags; bodies over 64 KiB are omitted from parsing/capture.

JSON fields containing password/secret/token/authorization/cookie/api-key names
are redacted recursively. Configured environment values are replaced in captured
text. Binary/non-JSON data is decoded as bounded UTF-8 text. Redaction is best
effort: user data or secrets outside recognized fields/configured values can be
recorded, especially raw stdout/stderr. Enable capture only for appropriate local
test data. Headers/query strings and exception locals remain excluded.

Output events use `kind=stdout` or `stderr`, with at most 2 KiB per write. They
share the same 1,000-event retention as requests/errors and are process-wide,
not reliably attributable to one request. MCP read access includes captured data
when explicitly enabled. Disabling capture does not delete already retained events.

[Documentation index](../README.md) · [Project overview](../../README.md)
