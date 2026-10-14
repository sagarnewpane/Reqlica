"""Expose draft editing and review. Human approval is local CLI-only."""

from fastapi import APIRouter
from reqlica.console.dependencies import (
    Revision,
    draft_operation,
    list_drafts,
    read_draft_file,
)
from reqlica.projects import drafts
from reqlica.projects.store import Endpoint

router = APIRouter(prefix="/api/projects/{project_id}/drafts", tags=["Drafts"])


class FileWrite(Revision):
    content: str


class EndpointWrite(Revision):
    endpoint: Endpoint


class EndpointRemove(Revision):
    method: str
    path: str


@router.post("")
def create(project_id: str) -> dict:
    return draft_operation(drafts.create_draft, project_id)


@router.get("")
def listing(project_id: str) -> dict:
    return draft_operation(list_drafts, project_id)


@router.get("/{draft_id}")
def inspect(project_id: str, draft_id: str) -> dict:
    return draft_operation(drafts.inspect_draft, project_id, draft_id)


@router.get("/{draft_id}/files")
def read(project_id: str, draft_id: str, path: str) -> dict:
    return draft_operation(read_draft_file, project_id, draft_id, path)


@router.put("/{draft_id}/files")
def write(project_id: str, draft_id: str, path: str, body: FileWrite) -> dict:
    return draft_operation(
        drafts.write_draft_file,
        project_id,
        draft_id,
        path,
        body.content,
        body.expected_revision,
    )


@router.delete("/{draft_id}/files")
def delete(project_id: str, draft_id: str, path: str, body: Revision) -> dict:
    return draft_operation(
        drafts.delete_draft_file, project_id, draft_id, path, body.expected_revision
    )


@router.put("/{draft_id}/endpoints")
def upsert(project_id: str, draft_id: str, body: EndpointWrite) -> dict:
    return draft_operation(
        drafts.upsert_endpoint,
        project_id,
        draft_id,
        body.endpoint,
        body.expected_revision,
    )


@router.delete("/{draft_id}/endpoints")
def remove(project_id: str, draft_id: str, body: EndpointRemove) -> dict:
    return draft_operation(
        drafts.remove_endpoint,
        project_id,
        draft_id,
        body.method,
        body.path,
        body.expected_revision,
    )
