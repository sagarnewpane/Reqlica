"""Write-only project variables. Never expose values through management APIs."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from reqlica.console.dependencies import draft_operation
from reqlica.projects.environment import (
    EnvironmentRevision,
    EnvironmentWrite,
    change_environment,
    get_environment,
)


class PrivateEnvironmentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                # FastAPI normally includes invalid input values in 422 details.
                raise HTTPException(
                    422,
                    "Invalid environment request. Check the revision, name, and value limits; values are never echoed.",
                    headers={"Cache-Control": "no-store"},
                ) from error
            except HTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise

        return private_handler


router = APIRouter(
    prefix="/api/projects/{project_id}/environment",
    tags=["Project environment"],
    route_class=PrivateEnvironmentRoute,
)


@router.get("")
def listing(project_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(get_environment, project_id)


@router.put("/{name}")
def write(
    project_id: str, name: str, body: EnvironmentWrite, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(
        change_environment, project_id, name, body.expected_revision, body.value
    )


@router.post("/{name}/generate")
def generate(
    project_id: str, name: str, body: EnvironmentRevision, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(
        change_environment, project_id, name, body.expected_revision, generate=True
    )


@router.delete("/{name}")
def remove(
    project_id: str, name: str, body: EnvironmentRevision, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(
        change_environment, project_id, name, body.expected_revision, remove=True
    )
