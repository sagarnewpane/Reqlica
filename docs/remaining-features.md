# Feature implementation status

Updated: 2026-10-25. The local dashboard features listed in the audit are now implemented.

| Feature | Where to use it | Behavior |
| --- | --- | --- |
| OpenAPI export/import | Contract viewer; new project chat | Revision-pinned JSON/YAML downloads; upload/review/select operations; original requirements persist with drafts and checks. |
| Project transfer | Projects → Import; Runtime → Project & history | Active-project ZIP, clone/import into a new directory, full workspace backup/restore, optional secret inclusion. |
| Traffic controls | Runtime → Traffic | Per-route delay, failure/empty responses, finite failure counts, route/test-user rate limits and counter reset. |
| Authentication and CORS | Runtime → API access | Bearer/API-key credentials with optional expiry; replace-all review; origins, methods, headers, credentials and preflight cache. |
| Baseline/reset | Runtime → Data baseline | Inspect/capture baseline and confirm live restore with schema checks and a safety backup. |
| Database migrations | Runtime → Migrations | Add/drop/rename/rebuild operations; SQL/impact preview on a copy; save plan into a draft; guarded live apply; history, rollback and interrupted-receipt recovery. |
| Route/source editing | Drafts → Route editor / Source files | Create/update/remove method/path registrations; handler selection; delete source files with revision checks. Moving a route uses add-new/remove-old. |
| Repair handoff | Draft check result; route editor; endpoint details | Open a new chat with the draft/endpoint and a repair prompt prepared for review. Sending remains explicit. |
| AI sample-data artifacts | Project chat | Inspect generated records, transfer to Sample data, validate again before insertion. |
| Request chains | Request tester → Request chains | Ordered requests, named response references, typed JSON substitution, encoded path values, expected statuses, stop-on-failure, stop-after-current, result report and saved chains. |
| Request conveniences | Request tester | Contract examples, inline header overrides, copy response/cURL and persistent saved requests. |
| Move/convert | Runtime → Project & history | Revision-pinned relocation and legacy-to-versioned conversion. |
| Published version history | Runtime → Project & history | List code snapshots; restore into a new unchecked draft based on the current published version. |
| Environment conveniences | Runtime → Environment | Generate/store a secure secret and select an existing variable for replacement. Secret values are not returned. |
| Package history | Runtime → Python packages | Recent selector and paged access to older saved operations. |
| Sample-data conveniences | Sample data | Copy JSON, insert record template, persistent named datasets and loading into the editor. |
| Connection/display | Settings | Persistent loopback console/runtime origins, connection health, published base-URL copy, system/light/dark theme. |

## Verification

- Temporary-project checks through the running Next.js proxy: saved library, traffic/counter reset, authentication, CORS, published requests, secret generation, historical restore and file deletion.
- Real temporary SQLite migration: preview, save, validation/tests, live apply, history and rollback; baseline capture/restore.
- Portable ZIP clone; full workspace backup/restore preserving saved artifacts; relocation; console/runtime health.
- Browser: traffic save, migration SQL preview and plan save, version/transfer controls, request-chain completion.
- Focused backend tests, request-chain substitution tests, proxy regression checks, TypeScript and lint.
- No paid AI calls were used for verification. This does not assert every configuration permutation has been exercised.

## Practical limits

- Imported specification context is capped at 24 KiB; upload at 128 KiB. Unsupported OpenAPI constructs remain explicit contract-check coverage gaps.
- Draft requests each use disposable data. Chains requiring persistent writes should target the published API.
- Saved requests/chains omit inline header values. Shared runtime headers apply during execution.
- Workspace restore preserves identity and requires its old registration to be removed first. It never overwrites a registered project or a populated destination.
- Historical code restore does not rewind SQLite data. Database rollback uses migration/baseline controls.
- Connection settings accept loopback origins for this local dashboard. Themes reuse the current design tokens.

Remote collaboration/authorization, hostile-code sandboxing, real-backend scenario
execution and a CI runner remain separate roadmap extensions, outside the local
workflow features requested here.
