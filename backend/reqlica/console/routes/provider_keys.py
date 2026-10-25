"""Private app credentials, with compatible aliases for older project clients."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from reqlica.console.dependencies import draft_operation
from reqlica.generation.credentials import ProviderKeyWrite, get_key, key_status, save_key
from reqlica.generation.schemas import ProviderEndpoint
from reqlica.generation.catalog import discover_models
from reqlica.generation.errors import GenerationError


class PrivateKeyRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Invalid provider key settings.", headers={"Cache-Control": "no-store"}) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise

        return private_handler


router = APIRouter(prefix="/api/projects/{project_id}/ai", tags=["Project AI settings"], route_class=PrivateKeyRoute)


@router.post("/key/status")
def status(project_id: str | None, body: ProviderEndpoint, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(key_status, project_id, body)


@router.post("/key/reveal")
def reveal(project_id: str | None, body: ProviderEndpoint, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    key = draft_operation(get_key, project_id, body)
    if key is None:
        raise HTTPException(404, "No API key is saved for this provider.", headers={"Cache-Control": "no-store"})
    return {"api_key": key.get_secret_value()}


@router.put("/key")
def write(project_id: str | None, body: ProviderKeyWrite, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(save_key, project_id, body, body.api_key)


@router.delete("/key")
def remove(project_id: str | None, body: ProviderEndpoint, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(save_key, project_id, body, None)


@router.post("/models")
def models(project_id: str | None, body: ProviderEndpoint, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    key = draft_operation(get_key, project_id, body)
    try:
        return discover_models(body, key)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


app_router = APIRouter(prefix="/api/ai", tags=["App AI settings"], route_class=PrivateKeyRoute)


@app_router.post("/key/status")
def app_status(body: ProviderEndpoint, response: Response) -> dict:
    return status(None, body, response)


@app_router.post("/key/reveal")
def app_reveal(body: ProviderEndpoint, response: Response) -> dict:
    return reveal(None, body, response)


@app_router.put("/key")
def app_write(body: ProviderKeyWrite, response: Response) -> dict:
    return write(None, body, response)


@app_router.delete("/key")
def app_remove(body: ProviderEndpoint, response: Response) -> dict:
    return remove(None, body, response)
