"""Review migrations and forward worker-owned maintenance operations."""

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from reqlica.cli import _mock_base_url
from reqlica.console.dependencies import draft_operation, gateway_operation
from reqlica.projects.migrations import MigrationPreview, MigrationApply, MigrationRollback, preview_migration, migration_history
from reqlica.projects.workspace import import_workspace
from reqlica.projects.transfer import MAX_ARCHIVE
from reqlica.runtime.relocation import Relocation
from reqlica.runtime.operations import WorkspaceExport
from reqlica.projects.store import get_project
from reqlica.projects.versions import code_directory, content_digest, project_lock

router = APIRouter(prefix="/api/projects", tags=["Maintenance"])


def maintenance_state(project_id: str):
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        return {"project_id": project_id, "directory": str(project.directory),
                "active_revision": content_digest(code_directory(project))}


@router.get("/{project_id}/maintenance")
def inspect_maintenance(project_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(maintenance_state, project_id)


@router.post("/{project_id}/relocate")
def relocate(project_id: str, body: Relocation):
    return gateway_operation(project_id, "relocate", body.model_dump())


@router.post("/{project_id}/workspace-export")
def archive(project_id: str, body: WorkspaceExport):
    try:
        reply = httpx.post(f"{_mock_base_url()}/api/projects/{project_id}/workspace-export", json=body.model_dump(), timeout=120, trust_env=False)
    except httpx.HTTPError as error:
        raise HTTPException(502, "Cannot reach gateway") from error
    if not reply.is_success:
        raise HTTPException(reply.status_code, reply.json().get("detail", "Archive failed"))
    return Response(reply.content, media_type="application/zip", headers={"Cache-Control":"no-store"})


@router.post("/workspace-import", status_code=201)
async def restore(request: Request, directory: str):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_ARCHIVE:
            raise HTTPException(413, "Archive exceeds 32 MiB")
    return await run_in_threadpool(draft_operation, import_workspace, bytes(content), directory)


@router.post("/{project_id}/database-migrations/preview")
def preview(project_id: str, body: MigrationPreview):
    return draft_operation(preview_migration, project_id, body)


@router.get("/{project_id}/database-migrations")
def history(project_id: str):
    return draft_operation(migration_history, project_id)


@router.post("/{project_id}/database-migrations/apply")
def apply(project_id: str, body: MigrationApply):
    return gateway_operation(project_id, "database-migrations/apply", body.model_dump())


@router.post("/{project_id}/database-migrations/rollback")
def rollback(project_id: str, body: MigrationRollback):
    return gateway_operation(project_id, "database-migrations/rollback", body.model_dump())


@router.post("/{project_id}/database-migrations/recover")
def recover(project_id: str, body: MigrationRollback):
    return gateway_operation(project_id, "database-migrations/recover", body.model_dump())
