"""Expose draft editing, review, and explicitly confirmed human approval."""

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from reqlica.console.dependencies import (
    Revision,
    draft_operation,
    list_drafts,
    read_draft_file,
)
from reqlica.projects import drafts
from reqlica.projects.database_setup import (
    SetupApply,
    SetupRevision,
    apply_setup,
    preview_setup,
)
from reqlica.projects.store import Endpoint

router = APIRouter(prefix="/api/projects/{project_id}/drafts", tags=["Drafts"])


class FileWrite(Revision):
    content: str


class EndpointWrite(Revision):
    endpoint: Endpoint


class EndpointRemove(Revision):
    method: str
    path: str


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)


class DraftRename(Revision):
    name: str = Field(min_length=1, max_length=100)


class ConfirmedRevision(Revision):
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class DraftDelete(ConfirmedRevision):
    pass


@router.post("/{draft_id}/approve")
def approve(
    project_id: str, draft_id: str, body: ConfirmedRevision, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(
        drafts.approve_draft, project_id, draft_id, body.expected_revision
    )


@router.post("/{draft_id}/recover")
def recover(project_id: str, draft_id: str, body: Revision) -> dict:
    return draft_operation(drafts.recover_draft, project_id, draft_id, body.expected_revision)


@router.post("")
def create(project_id: str, body: DraftCreate | None = None) -> dict:
    return draft_operation(drafts.create_draft, project_id, body.name if body else None)


@router.get("")
def listing(project_id: str) -> dict:
    return draft_operation(list_drafts, project_id)


@router.get("/{draft_id}")
def inspect(project_id: str, draft_id: str) -> dict:
    return draft_operation(drafts.inspect_draft, project_id, draft_id)


@router.patch("/{draft_id}")
def rename(project_id: str, draft_id: str, body: DraftRename) -> dict:
    return draft_operation(drafts.rename_draft, project_id, draft_id, body.name, body.expected_revision)


@router.delete("/{draft_id}")
def delete_saved_draft(project_id: str, draft_id: str, body: DraftDelete) -> dict:
    return draft_operation(drafts.delete_draft, project_id, draft_id, body.expected_revision, confirm=body.confirm)


@router.post("/{draft_id}/database/preview")
def database_preview(
    project_id: str, draft_id: str, body: SetupRevision, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(preview_setup, project_id, draft_id, body.expected_revision)


@router.post("/{draft_id}/database/apply")
def database_apply(
    project_id: str, draft_id: str, body: SetupApply, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(apply_setup, project_id, draft_id, body)


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
