"""Expose domain checks and disposable draft endpoint calls."""

from typing import Any

from fastapi import APIRouter
from pydantic import Field
from reqlica.console.dependencies import Revision, draft_operation
from reqlica.projects import drafts

router = APIRouter(
    prefix="/api/projects/{project_id}/drafts/{draft_id}", tags=["Checks"]
)


class EndpointCall(Revision):
    method: str
    path: str
    json_body: Any = None
    headers: dict[str, str] = Field(default_factory=dict)


@router.post("/validate")
def validate(project_id: str, draft_id: str, body: Revision) -> dict:
    return draft_operation(
        drafts.validate_draft, project_id, draft_id, body.expected_revision
    )


@router.post("/test")
def test(project_id: str, draft_id: str, body: Revision) -> dict:
    return draft_operation(
        drafts.test_draft, project_id, draft_id, body.expected_revision
    )


@router.post("/call")
def call(project_id: str, draft_id: str, body: EndpointCall) -> dict:
    return draft_operation(
        drafts.call_draft_endpoint,
        project_id,
        draft_id,
        body.expected_revision,
        body.method,
        body.path,
        body.json_body,
        body.headers,
    )
