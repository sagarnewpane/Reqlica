# Project environment variables

Use **Runtime → Environment** after opening a project. This supplies
configuration such as `JWT_SECRET` or `REQLICA_JWT_SECRET` to its Python code,
without putting real secrets in prompts, generated files, or browser storage.

## Configure a JWT signing key

1. Read the variable name from the handler or its missing-configuration error.
   Names are case-sensitive: if the code uses `os.getenv("JWT_SECRET")`, enter
   **`JWT_SECRET`**, not a differently named key.
2. Enter a value and choose **Save value…**, or use **Generate & save secure value…**
   for a JWT key. Generation creates 48 random bytes encoded as a 64-character
   URL-safe string, saves it server-side, and never sends the value to the browser.
3. Review and confirm the project/name. Existing values cannot be revealed; use
   **Replace** to supply a new value. Rotation may invalidate existing tokens.
4. Rerun draft **Validate** and **Run tests**. New checks receive current settings
   before Python starts and before importing handlers. Existing checks and approval
   become stale when settings change, even if source revision is unchanged.
5. Use **Start / restart API…** for already published code. This explicitly stops
   and starts the project worker and can interrupt requests. It never approves or
   publishes a draft. Activating a reviewed draft also starts its new worker with
   current settings through the normal explicit approval/activation workflow.

Saving settings does not restart workers, rewrite code, apply database plans,
create an account, or implement authentication. A running process keeps its old
environment until restarted. Its project status includes the loaded
`environment_revision`, so the dashboard can show a restart reminder.

## Storage and privacy

- Values are stored as **unencrypted local JSON** at
  `<project>/.reqlica/environment.json`, written atomically with mode `0600`.
  They are outside versioned source, draft snapshots, database backups, and AI
  context. Treat project-directory backups as sensitive: they can include this file.
- Settings read/write responses contain names, a revision, and status—not values.
  Even malformed/invalid settings requests use sanitized errors rather than
  FastAPI's normal echoed input details. Responses are `Cache-Control: no-store`.
- The dashboard masks typed values and clears them after a submission attempt,
  leaving Settings, or switching projects. Cancellation before submission keeps
  the input so you can edit it. Reqlica does not store values in local/session
  storage or provider profiles; your browser/password manager is independent.
- Project context and model input include **configured variable names only**.
  Names must not themselves contain secrets. Never paste a key into the generation
  prompt. AI can name a required variable and explain how to configure it without
  knowing its value.
- Workers and check subprocesses receive project overrides without mutating the
  console/gateway process environment or another project's configured values.
  Child processes still inherit ordinary host/server environment. An unconfigured
  or removed name may fall back to an inherited value. Do not put project secrets
  in Reqlica's repository-root `.env`; that file is for server/provider settings.
- Plain configured values are redacted from stored draft-check output and worker
  startup errors on a best-effort basis. This does not redact arbitrary handler
  responses, encoded secrets, or every application log/traceback. Trusted Python
  can read files/environment, make network calls, and leak credentials; it is not
  sandboxed. Do not use production credentials or expose management APIs remotely.

## Console API

Base: `/api/projects/{project_id}/environment`.

| Method/path             | Action                                                       |
| ----------------------- | ------------------------------------------------------------ |
| `GET`                   | Names and current configuration revision, never values.      |
| `PUT /{name}`           | Save/replace a value with `{expected_revision, value}`.      |
| `POST /{name}/generate` | Generate and save a secure value with `{expected_revision}`. |
| `DELETE /{name}`        | Remove the project override with `{expected_revision}`.      |

The revision comes from the most recent metadata response. Concurrent changes
return `409`; reload names before retrying. After a timeout, reload metadata
instead of blindly retrying a secret rotation—the write may have succeeded.
Each mutation returns `restart_required: true` and `checks_require_rerun: true`;
whether a worker is actually running is reported separately by the gateway.

Limits: 100 variables, 64-character ASCII identifier names, nonempty text values
up to 8 KiB each without NUL, and 128 KiB serialized configuration. Multiline
values are supported. Values are not shell-expanded or interpolated.

Process/control names are reserved: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`,
`PWD`, `OLDPWD`, `TMPDIR`, `TEMP`, `TMP`, `VIRTUAL_ENV`, `REQLICA_HOME`,
`GROQ_API_KEY`, `GROQ_API`, and names starting with `PYTHON`, `LD_`, `DYLD_`,
`MOCK_`, `CONSOLE_`, or `REQLICA_UI_` (case-insensitive reservation).
Normal application names such as `JWT_SECRET` and `REQLICA_JWT_SECRET` are allowed.
Provider credentials remain configured separately in the existing provider flow.

## Generated code

Project AI can ask clarification questions about fields, variable names, and
missing-configuration behavior in the same conversation. Answer those design
questions without providing secret values. Supply private values through the
environment settings after reviewing the generated code.
For ordinary CRUD/stateless APIs, it prefers no unnecessary environment variables.
For genuine JWT signing, it requires a stable configured secret rather than a
hardcoded fallback or a key generated per request/restart. Missing configuration
should produce a clear startup failure or an actionable `503`, naming the variable
but never its value. Generation does not automatically provision project secrets.

Tests may inject explicit **test-only** values with `unittest.mock.patch.dict`
before imports, or use saved development settings. Check results record the
configuration revision; changes during execution prevent storing a passing result.
Authentication correctness, token verification, and password hashing still require
meaningful behavior tests and human code review.

[Documentation index](../README.md) · [Project overview](../../README.md)
