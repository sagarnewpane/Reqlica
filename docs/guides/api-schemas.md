# Generated API schemas and Swagger

Reqlica generates OpenAPI from the **actual loaded FastAPI app**, not from a
separate description written by the LLM. Code remains the source of truth.

## Try it

1. Write typed handlers and register their draft routes.
2. Validate the draft:

   ```bash
   uv run reqlica draft validate "My API" <draft-prefix>
   ```

3. Ask the LLM for `get_api_schema` with the project/draft IDs. For one endpoint,
   also supply **both** `method` and its registered `path`, such as `POST /hello`.
4. Ask `get_draft_urls` for `docs_url` to open Swagger, or `openapi_url` for JSON.
   `uv run reqlica draft url "My API" <draft-prefix> --json` includes those links too.

For example, write `endpoints/hello.py` and register `POST /hello`:

```python
from pydantic import BaseModel, Field


class HelloInput(BaseModel):
    name: str = Field(min_length=1, examples=["Sagar"])


class HelloOutput(BaseModel):
    message: str


def hello(body: HelloInput) -> HelloOutput:
    return HelloOutput(message=f"Hello, {body.name}")
```

OpenAPI then describes a required JSON body with a required `name` string, its
minimum length and example, the named response field, and FastAPI's validation
error response. Typed query/path/header parameters, dependencies, nested models,
constraints, enums, and declared examples are preserved by FastAPI. Business
rules and error cases that are not declared in the schema are not inferred.

## Saved artifacts and revisions

Successful validation includes a `schema` summary: `status` (`complete` or
`incomplete`), `revision`, `artifact`, `warning_count`, and up to 20 warnings.
The full OpenAPI JSON contains all contract warnings in
`x-reqlica-contract-warnings`.

Files live **outside the draft and published code directories**:

```text
<project>/.reqlica/openapi/
  drafts/<draft-id>/<revision>.json
  drafts/<draft-id>/<revision>.metadata.json
  versions/<version-id>/<revision>.json
  versions/<version-id>/<revision>.metadata.json
```

Each primary JSON file is a normal OpenAPI document with Reqlica identity/revision
extensions. The sidecar records the revision and document checksum. Do not edit
generated files; change the Python and revalidate. Generation does not change the
code digest or cause an endless edit/validation cycle. Each document is limited
to 1 MiB, separately from bounded runner logs/results.

Edits invalidate validation and therefore invalidate schema access until the new
revision is validated. Old artifacts remain as history but are not served as the
current contract. Corrupt, missing, mismatched, or symlinked artifacts are rejected.
Schema inspection only reads verified files; it never imports handlers, runs
tests, clears an otherwise valid approval, or guesses a replacement schema.

Activation copies the validated document into a **separate published-version
artifact before switching the active pointer**. Editing a draft never updates the
published schema. New projects, converted legacy projects, or revisions approved
before this feature may have no published artifact yet: validate a draft and
activate it through the normal human approval flow first. No approval is automatic.

## Draft versus published documentation

| API                     | Swagger                                             | OpenAPI JSON                  | Requests go to                       |
| ----------------------- | --------------------------------------------------- | ----------------------------- | ------------------------------------ |
| Draft, console port     | `/api/projects/{project-id}/drafts/{draft-id}/docs` | Same prefix + `/openapi.json` | Disposable `/preview/...` routes     |
| Published, gateway port | `/api/projects/{project-id}/docs`                   | Same prefix + `/openapi.json` | Live `/mock/{project-id}/...` routes |

These are project-specific pages, separate from the servers' root management
`/docs` pages. Draft Swagger never points at the live mock gateway. Its schema
fetch is revision-pinned, and each operation has a prefilled
`X-Reqlica-Expected-Revision` header. Keep that value; edits cause `409` until you
revalidate and reload, rather than running an old form against changed code.
Ordinary convenience preview URLs remain current-at-request-time when the header
is omitted. Swagger assets use FastAPI's CDN defaults; JSON/MCP inspection still
works when those browser assets are unavailable.

Draft previews retain their existing limits: JSON request bodies, query/path
parameters, bounded JSON/text responses, and disposable data per request. Custom
request headers/authentication, cookies, streaming, and binary/form uploads are
not forwarded. The revision-pin header is tooling metadata, not a forwarded API
header. Use tests for multi-step stateful checks. Published Swagger requests use
**persistent live data** and are explicitly labelled accordingly.

## Incomplete contracts

Raw `Request` parsing, unconstrained parameters, and free-form bodies/responses
can hide expected fields. Reqlica flags these with the method, path, location,
and actionable guidance instead of inventing a JSON format. Use Pydantic input
and return models when fixed named fields are expected. Free-form contracts can
be intentional; do not change unrelated endpoints just to remove warnings.

Incomplete-contract warnings do not block publication. Validation can pass with `schema.status: incomplete`;
review/document those warnings before approval. Schema generation failures or
colliding route patterns that OpenAPI cannot represent do fail validation.

[Documentation index](../README.md) · [Project overview](../../README.md)
