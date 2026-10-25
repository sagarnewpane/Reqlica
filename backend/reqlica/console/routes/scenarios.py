"""Manage gateway mock authentication settings."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from reqlica.console.dependencies import draft_operation
from reqlica.projects.authentication import AuthWrite, get_authentication, write_authentication
from reqlica.projects.scenarios import ScenarioWrite, get_scenarios, write_scenarios

class PrivateAuthenticationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                raise HTTPException(422, "Invalid mock authentication settings; check mode, revision, headers and credentials.",
                                    headers={"Cache-Control": "no-store"}) from error
            except HTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise

        return private_handler


router = APIRouter(prefix="/api/projects/{project_id}/mock-auth", tags=["Mock authentication"], route_class=PrivateAuthenticationRoute)


@router.get("")
def read(project_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(get_authentication, project_id)


@router.put("")
def write(project_id: str, body: AuthWrite, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(write_authentication, project_id, body)


scenario_router = APIRouter(prefix="/api/projects/{project_id}/scenarios", tags=["Scenarios"])


@scenario_router.get("")
def read_scenarios(project_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(get_scenarios, project_id)


@scenario_router.put("")
def replace_scenarios(project_id: str, body: ScenarioWrite, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(write_scenarios, project_id, body)
