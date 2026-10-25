"""Portable project bundles through bounded raw ZIP uploads and downloads."""

from typing import Annotated
from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from reqlica.console.dependencies import draft_operation
from reqlica.projects.transfer import MAX_ARCHIVE, export_project, import_project

router = APIRouter(prefix="/api/projects", tags=["Project transfer"])


@router.get("/{project_id}/export")
def export(project_id: str):
    data = draft_operation(export_project, project_id)
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="reqlica-{project_id}.zip"',
        "Cache-Control": "no-store",
    })


@router.post("/import", status_code=201)
async def import_bundle(request: Request, directory: str,
                        clone: bool = False,
                        name: Annotated[str | None, Query(min_length=1, max_length=128)] = None):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_ARCHIVE:
            raise HTTPException(413, "Project bundle exceeds 32 MiB")
    return await run_in_threadpool(draft_operation, import_project, bytes(data), directory, clone=clone, name=name)
