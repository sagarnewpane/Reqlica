# Browser CORS settings

Configure cross-origin browser access per published project. Gateway CORS is off
by default, updates immediately without a restart, and does not change source,
authentication, quotas or draft approval. Settings persist in `.reqlica/cors.json`
and are included in project ZIP export/import. Older bundles without CORS settings
import with the policy disabled.

## Configuration

Read `GET /api/projects/{project_id}/cors` on the console, then replace settings
with `PUT` at the same path using the returned revision:

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

Only `expected_revision` is required; omitted settings take their defaults shown
above except `enabled` (false) and `allow_origins` (empty). Enabled CORS requires
at least one allowed origin. These are replacement writes, not patches. Stale
revisions return 409; invalid settings return 422. Successful responses use
`Cache-Control: no-store`. Disable with `enabled: false` and the current revision.

Origins are HTTP(S) scheme/host/optional port, with no path (including trailing
slash), user credentials, query or fragment. Scheme/host case and default ports
are normalized. IPv6 hosts use brackets; international domains must use ASCII
punycode. Explicit `null` origins and origin regexes are not supported. At most
100 origins are accepted; total settings are capped at 64 KiB.

A single `"*"` allows any origin when credentials are disabled. Credentialed CORS
requires explicit origins, allowed headers and exposed headers; wildcard values
in those lists are rejected. Header names are case-insensitive and must be valid
HTTP tokens. `allow_methods` requires unique uppercase supported methods;
`max_age` is 0–86,400 seconds. Safelisted browser headers remain allowed by the
underlying CORS implementation.

## Browser behavior

With enabled policy, an OPTIONS request carrying `Origin` and
`Access-Control-Request-Method` is handled as a preflight before worker startup,
authentication, rate limits or scenarios. Allowed preflights return 200 with the
configured headers and max age. Disallowed origins, methods or request headers
return 400. Preflight success describes policy, not route existence or worker
health: it may succeed while the worker is stopped. These intercepted preflights
are not entered into request history and have no gateway request ID.

Requests with just OPTIONS but no preflight headers follow normal endpoint/auth
handling. Simple/actual browser requests still execute through authentication and
quotas. Allowed origins receive CORS headers on normal responses, synthetic
responses and handled gateway/lifecycle errors, including 401, 429 and 503.
Specific origins are echoed with `Vary: Origin`, preserving other Vary values.
Default exposed headers let JavaScript inspect request IDs, scenario labels and
Retry-After. Customize `allow_headers` when using a custom API-key header.

Disallowed origins do not receive Access-Control-Allow-Origin. The underlying
request may still run: CORS controls browser response access, not authentication
or protection against command-line/other server clients. Requests with multiple
Origin headers are rejected when gateway CORS is enabled. Invalid stored settings
return 503 for Origin-bearing requests instead of guessing a permissive policy;
non-browser requests without Origin retain normal behavior.

Enabled gateway policy strips handler-provided Access-Control-* headers before
applying its own, so handlers cannot override a denied origin. When disabled,
Reqlica adds no gateway CORS policy and leaves any application-owned CORS behavior
unchanged. Server errors outside normal handled gateway responses are not
promised browser-readable CORS responses.

This applies only to `/mock/<project-id>` traffic. It does not enable browser
access to management APIs, demo/docs endpoints or disposable draft previews.
External MCP clients can inspect and replace CORS through the
[confirmed traffic-policy tools](mcp.md#project-operations). Built-in Project AI
can inspect but cannot edit it. Browser private-network access and
browser-specific permissions are separate from these origin settings.

[Documentation index](../README.md) · [Project overview](../../README.md)
