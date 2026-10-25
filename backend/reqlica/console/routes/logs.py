"""Read bounded request metadata and structured runtime errors."""

from typing import Annotated
from fastapi import APIRouter, Path, Query, Response
from reqlica.console.dependencies import draft_operation
from reqlica.projects.inspection import EventKind, read_logs
from reqlica.projects.capture import CaptureWrite, get_capture, write_capture

router = APIRouter(prefix="/api/projects/{project_id}/logs", tags=["Inspection"])


@router.get("/settings")
def settings(project_id: str):
    return draft_operation(get_capture, project_id)


@router.put("/settings")
def configure(project_id: str, body: CaptureWrite):
    return draft_operation(write_capture, project_id, body)


@router.get("")
def listing(project_id: str, response: Response,
            limit: Annotated[int, Query(ge=1, le=100)] = 50,
            before_id: Annotated[int | None, Query(ge=1)] = None,
            kind: EventKind | None = None,
            request_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(read_logs, project_id, limit, before_id, kind, request_id)


@router.get("/{event_id}")
def detail(project_id: str, event_id: Annotated[int, Path(ge=1)], response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(read_logs, project_id, event_id=event_id)
