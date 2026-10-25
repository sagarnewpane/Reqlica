# OpenAPI and Swagger

Reqlica generates OpenAPI from the loaded FastAPI app. Use typed parameters,
Pydantic request/response models, and declared examples to describe the contract.
Undeclared business rules cannot be inferred.

## Try it

1. Save handlers and register their draft routes.
2. Validate the draft.
3. Use `get_api_schema` to read the contract or `get_draft_urls` for Swagger links.
   CLI: `uv run reqlica draft url PROJECT_ID DRAFT_ID --json`.

For one operation, `get_api_schema` needs both `method` and registered `path`.
Omit both to read the full document.

## Draft versus published documentation

| Version                 | Swagger path                                | Request target       |
| ----------------------- | ------------------------------------------- | -------------------- |
| Draft, console port     | `/api/projects/{id}/drafts/{draft_id}/docs` | Disposable preview   |
| Published, gateway port | `/api/projects/{id}/docs`                   | Persistent live data |

Replace `/docs` with `/openapi.json` for JSON. These differ from each server's
root `/docs`, which describes management APIs.

Draft Swagger pins `X-Reqlica-Expected-Revision`. After edits, revalidate and
reload. Each preview request discards data changes; use behavior tests for
multi-step flows. Preview limits exclude streaming, uploads, and cookie sessions.
Swagger UI assets use a CDN; JSON remains available without those assets.

## Artifacts and warnings

Saved schemas live in `<project>/.reqlica/openapi/`, outside editable source.
Edits invalidate current draft schema access. Activation copies the checked
schema into the published version. Reads verify artifacts without executing code.
A schema document is capped at 1 MiB.

Raw `Request` parsing and free-form types can leave the contract incomplete.
Validation reports `schema.status`, warning counts, and
`x-reqlica-contract-warnings`. Warnings alone do not block publication;
schema-generation failures and route collisions do.

[Documentation](../README.md)
