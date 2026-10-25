# Gateway scenarios

Simulate delays, failures, and empty lists on published mock routes. Settings
apply immediately and persist independently of drafts and code approval.

Read `GET /api/projects/{project_id}/scenarios`, then replace all rules with `PUT`:

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

Use the registered method and route pattern. Query strings do not affect matching.
Absent routes stay absent; their rules are dormant until the route exists.

- `delay_ms`: 0–30,000; startup and handler time are additional.
- `response`: `passthrough`, `failure`, or `empty` (HTTP 200 with `[]`).
- Failures: status 400–599, JSON body up to 16 KiB, optional finite count up to 1,000,000.
- Omit `failure_count` for persistent failure. After exhaustion, requests pass
  through with any configured delay.
- Optional `rate_limit`: see [rate limits](rate-limits.md).

At most 100 rules. Replacing settings resets all counters, even if rules are
unchanged. `rules: []` disables them. Stale revisions return 409.

Authentication precedes quota and failure reservation. Rejected credentials do not
consume counters. Cancelled requests still consume reservations; in-flight requests
keep their chosen action after settings change. Synthetic responses skip handler
execution, though worker startup may occur. Rules/counters persist in
`.reqlica/scenarios.json` across restarts.

Management/docs/demo routes and drafts are outside this policy.
[Scenario reset](reset.md#reset-scenario-counters) rearms counters without changing rules.

[Documentation](../README.md)
