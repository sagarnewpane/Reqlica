# Rate limits

Add a quota to a [scenario rule](scenarios.md):

```json
{
  "expected_revision": "<revision from GET /scenarios>",
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

Limits apply immediately to published gateway routes. Omit/null `rate_limit`
to disable a rule's quota.

- `requests`: 1–1,000,000; `window_seconds`: 1–86,400.
- `scope: "route"` shares a quota across callers for the registered pattern.
- `scope: "test_user"` uses `X-Reqlica-Test-User`: one case-sensitive value,
  1–128 printable ASCII characters without spaces. Invalid/missing values return 400.

Test-user labels are simulation identities, not authenticated users.
A fixed window begins with its first admitted request. Exceeding it returns 429,
`rate_limit_exceeded`, and `Retry-After`. Rejections do not extend the window.
Admitted requests count even when cancelled or answered with a failure.
Authentication precedes quotas; quota rejection skips scenario delays/failures.

Counters persist with scenario settings. Replacing settings resets all quotas;
[scenario reset](reset.md) also rearms failure counts. At most 1,000 active quota
buckets are retained per project; new identities beyond capacity get 429 until expiry.
Management/docs/demo URLs and drafts are excluded.

[Documentation](../README.md)
