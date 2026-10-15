"""Generated draft contracts and Swagger pointed only at disposable preview URLs."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from reqlica.console.dependencies import draft_operation
from reqlica.projects.schemas import get_api_schema, serving_document

router = APIRouter(
    prefix="/api/projects/{project_id}/drafts/{draft_id}", tags=["Draft schemas"]
)
RevisionPin = Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")]


@router.get("/openapi.json", name="draft_openapi", include_in_schema=False)
def openapi(
    project_id: str, draft_id: str, request: Request, revision: RevisionPin = None
):
    result = draft_operation(get_api_schema, project_id, draft_id)
    if revision is not None and revision != result["revision"]:
        raise HTTPException(
            409, "Draft schema changed; revalidate and reload documentation"
        )
    base = f"{request.scope.get('root_path', '')}/api/projects/{project_id}/drafts/{draft_id}/preview"
    return JSONResponse(
        serving_document(result, base, preview=True),
        headers={
            "Cache-Control": "no-store",
            "X-Reqlica-Draft-Revision": result["revision"],
        },
    )


@router.get("/docs", include_in_schema=False)
def docs(project_id: str, draft_id: str, request: Request):
    result = draft_operation(get_api_schema, project_id, draft_id)
    url = request.url_for(
        "draft_openapi", project_id=project_id, draft_id=draft_id
    ).include_query_params(revision=result["revision"])
    response = get_swagger_ui_html(
        openapi_url=str(url), title="Reqlica draft API — disposable preview"
    )
    response.headers["Cache-Control"] = "no-store"
    return response
