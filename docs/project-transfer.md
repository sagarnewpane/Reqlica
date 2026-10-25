# Project export and import

Export the active project as a portable ZIP bundle, then import it into a **new**
absolute server-side directory. Import validates the archive before creating the
project and never imports/runs Python handlers, starts workers or grants approval.
The management APIs also back [MCP archive and relocation tools](mcp.md#project-operations).
MCP uses server-local ZIP paths and reviewed file digests, with human confirmation;
it does not upload/download binary data through model context.

## Download a bundle

```bash
curl --fail http://127.0.0.1:3000/api/projects/PROJECT_ID/export \
  --output project.zip
```

`GET /api/projects/{project_id}/export` returns `application/zip`, a download
filename and `Cache-Control: no-store`. The export contains:

- Active source, route manifest, tests, project notes and declarative schema plans.
- A consistent SQLite backup including committed WAL contents.
- Scenario rules; counters and quota windows start fresh on import.
- Mock authentication settings and credential hashes (not plaintext credentials).
- Per-project browser CORS settings (older bundles default to disabled).
- The current data baseline, if configured.
- Original project ID, name, active version and source revision/checksums.
- Environment variable **names** so users know which private values to configure.

An export is an active-project snapshot, not an archive of the entire workspace.
Old versions, unpublished drafts, checks/approvals, cached OpenAPI artifacts,
conversations, request history, recovery backups and private environment values
are excluded. Older baseline files are excluded. Only the current baseline travels
with the project. Published Swagger/OpenAPI caches must be regenerated through
normal draft validation/activation; saved source/tests are preserved.

Source and database snapshots are taken under the project management lock, but
running workers may continue changing records. SQLite supplies one consistent
committed database snapshot, not a promise that all external state freezes at the
same instant. Arbitrary handler globals, external services and non-SQLite files
outside the active source tree are not bundled.

## Import the ZIP

```bash
curl --fail -X POST \
  'http://127.0.0.1:3000/api/projects/import?directory=/absolute/new/project' \
  -H 'Content-Type: application/zip' --data-binary @project.zip
```

`directory` must be a new absolute path whose parent already exists. Existing
folders, including empty ones, are rejected. Import preserves the project ID by
default, so an already registered ID returns 409 without changing files. For a
separate copy, add `clone=true`; optional `name=NewName` changes the display name.
URL-encode query values containing spaces or other special characters.

A successful import returns HTTP 201 with `project`, `status: "imported"`, `cloned`,
`source_revision`, and `environment_names_required`. Configure those environment
values privately before starting the imported API. Credentials themselves remain
valid against the imported mock-auth hashes; no credential values can be recovered
from the bundle. Treat bundles as private: records, hashes, baseline data and
secrets hardcoded into source can be present.

The project is registered only after preparing/validating all files. No source or
SQLite data in an existing project is overwritten. A failed installation removes
its new directory and registration. The next mock request can lazily start an
imported worker using the normal gateway behavior; inspect trusted source and set
variables before sending that request.

Cloning changes metadata IDs, not Python source or data literals. Generated code
and tests may contain the original project ID; inspect/update such references when
using a clone. For migration with stable IDs, preserve the ID and import on the
other installation. To free an ID on the same installation, unregister through
the gateway's existing deletion API with `delete_files: false`; do not manually
edit a running gateway's registry behind its worker cache.

Import/export copies a project; it does not relocate an existing registration or
remove the original directory. Stable-ID same-filesystem moves use the separate [relocation API](maintenance.md).

## Format and validation

`reqlica-project-v1` contains `bundle.json`, `code/*`, `data.sqlite`,
`settings.json`, and optional `baseline.sqlite`. File checksums and the full source
revision must match. Checksums detect corruption; they are not signatures or proof
that the supplied Python is safe.

Import rejects traversal/absolute/backslash paths, duplicate ZIP entries,
symlinks/special files, encrypted entries, missing manifest/handler files, invalid
settings, inconsistent baseline metadata and invalid SQLite integrity/foreign keys.
It extracts files individually into a temporary staging directory, never with ZIP
`extractall`. Source execution and dependency installation are not validation steps.

Limits: 32 MiB uploaded/compressed ZIP, 128 MiB unpacked, 2,000 content files,
1 MiB bundle metadata. Large source/database snapshots are rejected. Oversized
HTTP uploads return 413; invalid bundles/conflicts return 409; missing projects 404.
Temporary snapshots are removed after the operation. Imported database/settings
files are created privately. No automatic compression/deletion of old source
projects or retained baselines is performed.

For all historical versions, drafts and retained state, use the full workspace
archive endpoints described in [maintenance](maintenance.md).
