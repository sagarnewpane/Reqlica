"""Configure browser access to one published mock API."""

from fastapi import APIRouter, Response
from reqlica.console.dependencies import draft_operation
from reqlica.projects.cors import CorsWrite, get_cors, write_cors

router = APIRouter(prefix="/api/projects/{project_id}/cors", tags=["Project CORS"])


@router.get("")
def read(project_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(get_cors, project_id)


@router.put("")
def write(project_id: str, body: CorsWrite, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(write_cors, project_id, body)
