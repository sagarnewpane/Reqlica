# Mock rate limits

Add `rate_limit` to a rule in the existing console scenarios API:
`GET` / `PUT /api/projects/{project_id}/scenarios`. Full replacement still requires
`expected_revision` from GET. Limits apply immediately to published mock gateway
routes. Omit/null `rate_limit` to disable quotas for that rule.

Example PUT body:

```json
{
  "expected_revision": "<revision from GET>",
  "rules": [
    {
      "method": "GET",
      "path": "/books/{book_id}",
      "rate_limit": {
        "requests": 5,
        "window_seconds": 60,
        "scope": "test_user"
      }
    }
  ]
}
```

- `requests`: 1–1,000,000 admitted requests per window.
- `window_seconds`: 1–86,400 seconds.
- `scope: "route"` (default): all callers share one quota for the registered
  method and pattern. `/books/1` and `/books/2` share `/books/{book_id}`'s quota;
  query strings do not create separate quotas.
- `scope: "test_user"`: each `X-Reqlica-Test-User` header value has an independent
  quota for this route. Send exactly one value containing 1–128 printable ASCII
  characters without spaces. Values are case-sensitive. Missing, duplicate or
  malformed values return 400 with `detail: "invalid_test_user"`, without consuming
  quota. Stored counters use hashed identities; user header values are not stored.

Test-user identities are caller-selected simulation labels, not authenticated
users. Changing the label creates a separate quota; use this only for mock client
behavior testing. Gateway authentication and handler authentication remain separate.

## Windows and responses

A fixed window begins with the first admitted request for a route/user, using the
server's wall clock. Exactly at its reset time, a new window can begin. Exceeding
the quota returns HTTP 429, `{"detail":"rate_limit_exceeded"}` and integer
`Retry-After` seconds (rounded up, minimum 1). Rejections use
`Cache-Control: no-store`; HEAD responses have no body. Rejected requests neither
extend the window nor consume additional quota.

Order: worker lifecycle checks, gateway authentication, quota reservation, scenario
failure reservation, delay, then handler forwarding or a synthetic response.
Authentication rejections do not consume quota. Admitted requests count even if
cancelled later or answered with a scenario failure/empty response or handler error.
429 and invalid-user responses bypass scenario delays and finite failure counts.
Other projects and registered routes retain independent quotas. Unknown routes and
methods keep their normal behavior.

All quotas and failure counts are updated under one cross-process file lock in
`.reqlica/scenarios.json`. Counts persist across worker/gateway restarts and code
publication. Counters do not change the configuration revision. Replacing scenario
settings resets **all** quota windows and finite failure counts, even when submitting
the same rules. Removing rules clears their quotas.
The explicit [scenario reset API](reset.md) also clears quotas and rearms finite
failure counts while preserving all configured rules.

At most 1,000 active route/user quota buckets are retained per project. Expired
buckets are discarded during quota checks. If a new identity would exceed this
bound, it receives 429 with `detail: "rate_limit_capacity"` and a `Retry-After`
pointing to the earliest bucket expiry; existing identities retain their quotas.

These controls do not apply to management/docs/demo URLs, draft previews or draft
check calls. External MCP clients can change rules or reset quotas through the
[confirmed traffic-policy tools](mcp.md#project-operations); built-in Project AI
has inspection only. See [scenarios](scenarios.md)
for route matching, response configuration, storage limits and concurrency details.

[Documentation index](../README.md) · [Project overview](../../README.md)
