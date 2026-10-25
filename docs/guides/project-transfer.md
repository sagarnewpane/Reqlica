# Project export and import

An active-project bundle copies published code, SQLite data, policies, the current
baseline, and required environment names. It excludes private environment values,
old versions, drafts, conversations, checks, logs, and installed packages.
Use [workspace archives](maintenance.md#full-workspace-archives) for history.

## Export

```bash
curl --fail http://127.0.0.1:3000/api/projects/PROJECT_ID/export \
  --output project.zip
```

SQLite backup provides a consistent committed snapshot; running workers may
continue writing afterward. External services and arbitrary globals are not captured.

## Import

```bash
curl --fail -X POST \
  'http://127.0.0.1:3000/api/projects/import?directory=/absolute/new/project' \
  -H 'Content-Type: application/zip' --data-binary @project.zip
```

The destination must not exist; its parent must exist. Paths refer to the server.
Import preserves identity by default and rejects an already registered ID.
Add `clone=true` for a new ID and optional `name=NewName`. Encode query values.

Import validates/stages files without running handlers or starting workers.
Configure required private values and inspect source before sending a live request,
which can start the worker. Cloning does not rewrite IDs hardcoded in source/data.
Scenario counters start fresh; mock credentials retain their hashes. Regenerate
published OpenAPI through normal draft validation and activation.

## Format and limits

`reqlica-project-v1` contains `bundle.json`, `code/`, `data.sqlite`, `settings.json`,
and optional `baseline.sqlite`. Import checks paths, checksums, configuration,
SQLite integrity, and foreign keys. Checksums are not signatures or proof of safe code.

Limits: 32 MiB compressed, 128 MiB unpacked, 2,000 content files, 1 MiB metadata.
Existing directories are never overwritten. Bundles can contain sensitive records,
credential hashes, and hardcoded secrets. Installed packages are not included.
Export/import copies projects; [relocation](maintenance.md#relocate-a-project) moves them.

[Documentation](../README.md)
