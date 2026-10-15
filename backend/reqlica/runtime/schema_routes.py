"""Published API docs use the gateway origin and only the active version's schema."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from reqlica.projects.schemas import get_api_schema, serving_document

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Published schemas"])


def _read(project_id: str) -> dict:
    try:
        return get_api_schema(project_id)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(500, str(error)) from error


@router.get("/openapi.json", name="published_openapi", include_in_schema=False)
def openapi(
    project_id: str,
    request: Request,
    revision: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
):
    result = _read(project_id)
    if revision is not None and revision != result["revision"]:
        raise HTTPException(409, "Published version changed; reload documentation")
    base = f"{request.scope.get('root_path', '')}/mock/{project_id}"
    return JSONResponse(
        serving_document(result, base), headers={"Cache-Control": "no-store"}
    )


@router.get("/docs", include_in_schema=False)
def docs(project_id: str, request: Request):
    result = _read(project_id)
    url = request.url_for(
        "published_openapi", project_id=project_id
    ).include_query_params(revision=result["revision"])
    response = get_swagger_ui_html(
        openapi_url=str(url), title="Reqlica published API — live data"
    )
    response.headers["Cache-Control"] = "no-store"
    return response
