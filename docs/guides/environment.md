# Project environment variables

Open **Runtime → Environment** to set handler configuration such as `JWT_SECRET`.
Use the exact name the code reads. Save a value or choose
**Generate & save secure value…** to generate a 48-byte random value server-side.
Confirm the project/name, rerun checks, and restart an already published worker.
Saving values does not restart it or publish code.

## Storage and behavior

- Values are local plaintext in `<project>/.reqlica/environment.json`, with private
  permissions. They are outside versioned source and normal AI context.
- Read responses contain names and a revision, never values. Existing values
  cannot be revealed; replace them when needed.
- Checks and new workers receive current values. Running workers retain old values
  until restart. Changes invalidate checks and approval.
- Host environment is still inherited. Removing an override may expose an inherited value.
- Do not paste secrets into chat or put them in source. Redaction is best effort;
  trusted Python can still read and leak them. Backups can contain private values.

## API

Prefix: `/api/projects/{project_id}/environment`.

| Method/path             | Body                                 |
| ----------------------- | ------------------------------------ |
| `GET`                   | No body; returns names and revision. |
| `PUT /{name}`           | `{expected_revision, value}`         |
| `POST /{name}/generate` | `{expected_revision}`                |
| `DELETE /{name}`        | `{expected_revision}`                |

Stale writes return 409. After an uncertain save/rotation, reload metadata before
retrying. Mutations report that restart and fresh checks are required.

Limits: 100 variables, 64-character ASCII names, nonempty values up to 8 KiB,
and 128 KiB total configuration. Values are not shell-expanded.
Process names such as `PATH`, `HOME`, `REQLICA_HOME`, provider credentials, and
Python/loader/server control prefixes are reserved. Normal app names such as
`JWT_SECRET` are allowed. See `/docs` for request validation.

[Documentation](../README.md)
