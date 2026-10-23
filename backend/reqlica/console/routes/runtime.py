"""Forward publication and legacy conversion to the owning mock gateway."""

from fastapi import APIRouter
from pydantic import Field
from reqlica.console.dependencies import Revision, gateway_operation
from reqlica.projects.reset import DataReset

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Runtime"])


class Activation(Revision):
    draft_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@router.post("/activate")
def activate(project_id: str, body: Activation) -> dict:
    return gateway_operation(project_id, "activate", body.model_dump())


@router.post("/convert")
def convert(project_id: str) -> dict:
    return gateway_operation(project_id, "convert")


@router.post("/data-reset")
def reset_data(project_id: str, body: DataReset) -> dict:
    return gateway_operation(project_id, "data-reset", body.model_dump())
