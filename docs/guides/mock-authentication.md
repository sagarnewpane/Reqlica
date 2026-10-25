# Mock authentication

Gateway requests can require an opaque bearer token or API key. This simulates
client access; it does not implement users, JWT verification, or management auth.
Generated handler authentication is separate, and both checks can apply.

Read `GET /api/projects/{project_id}/mock-auth`, then replace settings with `PUT`
at the same path:

```json
{
  "expected_revision": "<revision from GET>",
  "mode": "bearer",
  "credentials": [{ "name": "tester", "value": "test-token" }]
}
```

Send `Authorization: Bearer test-token`. For API keys, use `mode: "api_key"`
and a dedicated `header_name` (default `X-API-Key`). Disable with `mode: "none"`
and `credentials: []`. Changes apply immediately without restarting workers.

- At most 100 credentials; values are 1–256 printable ASCII characters without spaces.
- Optional `expires_at` must include a timezone. Names and values must be unique.
- GET returns names/expiration, never values or hashes. Keep values locally.
- Missing, invalid, duplicate, or expired credentials return 401. Invalid settings
  return 503; stale writes return 409. Worker startup may occur before rejection.

Settings persist in `.reqlica/mock-auth.json` as credential hashes, outside code
approval. Use synthetic credentials. Enabled CORS preflights bypass this check;
management/docs/demo routes and disposable drafts are outside the policy.

[Documentation](../README.md)
