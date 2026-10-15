"""Human-friendly draft preview URLs, separate from revision-explicit check APIs."""

import json
import re

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from reqlica.console.dependencies import draft_operation
from reqlica.projects import drafts
from starlette.concurrency import run_in_threadpool

router = APIRouter(
    prefix="/api/projects/{project_id}/drafts/{draft_id}", tags=["Draft preview"]
)
_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
    "set-cookie",
}


def _call_current(
    project_id: str,
    draft_id: str,
    method: str,
    path: str,
    body,
    expected_revision: str | None = None,
) -> dict:
    revision = expected_revision
    if revision is None:
        revision = drafts.inspect_draft(project_id, draft_id)["revision"]
    # No retries: edits between inspection and execution remain a revision conflict.
    return drafts.call_draft_endpoint(
        project_id, draft_id, revision, method, path, body
    )


@router.api_route(
    "/preview/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"],
)
async def preview(
    project_id: str, draft_id: str, path: str, request: Request
) -> Response:
    """Call the current draft with disposable data; never approve or activate it.

    Supports JSON request bodies and bounded JSON/text responses. Headers, cookies,
    streaming, and binary request/response fidelity are not part of this preview.
    """
    body = None
    expected_revision = request.headers.get("x-reqlica-expected-revision")
    if expected_revision is not None and not re.fullmatch(
        r"[0-9a-f]{64}", expected_revision
    ):
        raise HTTPException(400, "Invalid X-Reqlica-Expected-Revision")
    raw = await request.body()
    if raw:
        if len(raw) > 1_048_576:
            raise HTTPException(413, "Preview JSON body exceeds 1 MiB")
        media_type = (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise HTTPException(415, "Draft previews accept JSON request bodies only")
        try:
            body = json.loads(raw)
        except ValueError as error:
            raise HTTPException(400, "Invalid preview JSON body") from error
    target = "/" + path
    if request.url.query:
        target += "?" + request.url.query
    result = await run_in_threadpool(
        draft_operation,
        _call_current,
        project_id,
        draft_id,
        request.method,
        target,
        body,
        expected_revision,
    )
    if result["status"] != "passed":
        status = {"failed": 422, "timeout": 504}.get(result["status"], 502)
        return JSONResponse({"detail": result}, status_code=status)
    headers = {key.lower(): value for key, value in result["headers"].items()}
    excluded = _HOP_HEADERS | {
        name.strip().lower() for name in headers.get("connection", "").split(",")
    }
    headers = {key: value for key, value in headers.items() if key not in excluded}
    headers["x-reqlica-draft-revision"] = result["revision"]
    headers["x-reqlica-preview"] = "true"
    headers["cache-control"] = "no-store"
    # The runner returns parsed JSON where possible; only non-JSON text is raw.
    is_json = "json" in headers.get("content-type", "").lower()
    if result["status_code"] in {204, 304} or request.method == "HEAD":
        return Response(status_code=result["status_code"], headers=headers)
    if isinstance(result["body"], str) and not is_json:
        return Response(
            result["body"], status_code=result["status_code"], headers=headers
        )
    return JSONResponse(
        result["body"], status_code=result["status_code"], headers=headers
    )
