# Mock authentication

Published requests under `/mock/<project-id>` can require a configured bearer
credential or API key. Authentication is disabled by default and configured per
project through the console management API. Changes apply to the next request
without restarting workers or changing approved code.

This simulates client authentication for testing. It does not implement JWT
issuance/signature verification, users, roles, or management API authentication.
Generated handlers can still implement their own authentication; a request must
pass both checks when both are enabled. These settings apply to every method and
route under the project's mock gateway prefix, including ordinary OPTIONS.
Enabled [CORS preflights](cors.md) are answered before authentication. The demo route,
management/docs URLs, disposable draft previews and draft check calls are outside
this gateway policy.

## Configuration API

`GET /api/projects/{project_id}/mock-auth` returns `revision`, `mode`, `header_name`
and credential names/expiration times. Credential values and stored hashes are
never returned. `PUT` at the same path replaces the complete configuration and
requires its current `expected_revision`; stale writes return 409. Keep credential
values locally if you need to replace settings later. To revoke a credential,
replace the list without it. Missing projects return 404; invalid input returns
422 with values omitted. Responses use `Cache-Control: no-store`.

Example PUT body, using the revision from GET:

```json
{
  "expected_revision": "<revision from GET>",
  "mode": "bearer",
  "credentials": [
    { "name": "tester", "value": "test-token" },
    {
      "name": "expired",
      "value": "expired-token",
      "expires_at": "2020-01-01T00:00:00Z"
    }
  ]
}
```

Send `Authorization: Bearer test-token`. For API keys, set `mode` to `api_key` and
send the credential in `header_name` (default `X-API-Key`). Header names are
case-insensitive; credential values are case-sensitive. API keys must use a
dedicated header rather than Host, Authorization, cookies or transport headers.
Each credential has a unique name and value; at most 100 credentials are allowed.
Values contain 1–256 printable ASCII characters without spaces. Optional
`expires_at` must include a timezone; expiration is checked on every request.

Missing, invalid, duplicate-header and expired credentials return 401 before
handler requests are forwarded. Worker startup and Python imports may still occur. JSON `detail` is `missing_credentials`, `invalid_credentials`
or `expired_credentials`. Bearer failures include `WWW-Authenticate: Bearer`.
An unrecognized token is invalid, including unconfigured JWTs; opaque credentials
are matched exactly. Invalid/unreadable settings fail closed with 503.

Disable with `mode: "none"`, `credentials: []` and the latest revision.
Configuration is stored atomically with restricted permissions in
`.reqlica/mock-auth.json`, outside versioned source, using SHA-256 credential
hashes. Use synthetic test credentials, never production passwords or tokens.
Settings persist across restarts and publication; they are not part of a draft
revision or code approval. No AI/MCP tool changes these settings.

[Documentation index](../README.md) · [Project overview](../../README.md)
