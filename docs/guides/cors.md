# Browser CORS settings

CORS controls which browser origins can read published mock responses. It is
separate from authentication and does not prevent non-browser clients calling APIs.
It is disabled by default.

Read `GET /api/projects/{project_id}/cors`, then replace settings with `PUT`:

```json
{
  "expected_revision": "<revision from GET>",
  "enabled": true,
  "allow_origins": ["http://localhost:5173"],
  "allow_methods": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
  "allow_headers": [
    "Content-Type",
    "Authorization",
    "X-API-Key",
    "X-Reqlica-Test-User"
  ],
  "expose_headers": [
    "X-Reqlica-Request-ID",
    "X-Reqlica-Scenario",
    "Retry-After"
  ],
  "allow_credentials": false,
  "max_age": 600
}
```

Origins include scheme, host, and optional port, without paths or trailing slash.
Enabled policy needs an origin. `"*"` is allowed only without credentials;
credentialed lists need explicit values. At most 100 origins and 64 KiB of settings.
`max_age` allows 0–86,400 seconds. Writes replace the policy; stale revisions return 409.

Changes apply immediately and persist in `.reqlica/cors.json` and project exports.
Disable with `enabled: false` and the current revision.

Enabled preflights run before workers, auth, quotas, and scenarios. A successful
preflight does not prove a route exists or a worker is healthy. Actual requests
still follow access rules; a denied origin may execute but cannot read the response.
Gateway CORS replaces handler CORS headers when enabled. The policy covers only
`/mock/<project-id>`, not management APIs or draft previews.

[Documentation](../README.md)
