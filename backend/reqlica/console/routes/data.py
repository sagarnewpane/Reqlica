"""Data-only generation, disposable validation, and explicitly confirmed appends."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import SecretStr
from reqlica.console.dependencies import activity_operation, draft_operation
from reqlica.generation.data_schemas import (
    DataImportRequest,
    DataValidationRequest,
    MockDataRequest,
)
from reqlica.generation.errors import GenerationError
from reqlica.generation.mock_data import generate_mock_data
from reqlica.projects.mock_data import database_schema, insert_data, validate_data

router = APIRouter(prefix="/api/projects/{project_id}/data", tags=["Mock data"])


def operation(response: Response, function, *args):
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(function, *args)
    except GenerationError as error:
        raise HTTPException(
            error.status_code, error.detail, headers={"Cache-Control": "no-store"}
        ) from error


@router.get("/schema")
def schema(project_id: str, response: Response) -> dict:
    return operation(response, database_schema, project_id)


@router.post("/generate")
def generate(
    project_id: str,
    body: MockDataRequest,
    response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
    activity_id: Annotated[str | None, Header(alias="X-Reqlica-Activity-ID", pattern=r"^[a-f0-9]{32}$")] = None,
) -> dict:
    return operation(
        response,
        activity_operation,
        project_id,
        activity_id,
        generate_mock_data,
        project_id,
        body,
        SecretStr(groq_key) if groq_key else None,
    )


@router.post("/validate")
def validate(project_id: str, body: DataValidationRequest, response: Response) -> dict:
    return operation(response, validate_data, project_id, body)


@router.post("/insert")
def insert(project_id: str, body: DataImportRequest, response: Response) -> dict:
    return operation(response, insert_data, project_id, body)
