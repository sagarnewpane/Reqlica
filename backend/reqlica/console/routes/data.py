"""Disposable data validation and explicitly confirmed appends."""

from fastapi import APIRouter, HTTPException, Response
from reqlica.console.dependencies import draft_operation
from reqlica.generation.data_schemas import (
    DataImportRequest,
    DataValidationRequest,
    RelatedDataValidationRequest,
    RelatedDataImportRequest,
)
from reqlica.generation.errors import GenerationError
from reqlica.projects.related_data import validate_related_data, insert_related_data
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




@router.post("/validate")
def validate(project_id: str, body: DataValidationRequest, response: Response) -> dict:
    return operation(response, validate_data, project_id, body)


@router.post("/insert")
def insert(project_id: str, body: DataImportRequest, response: Response) -> dict:
    return operation(response, insert_data, project_id, body)




@router.post("/related/validate")
def validate_related(project_id: str, body: RelatedDataValidationRequest, response: Response) -> dict:
    return operation(response, validate_related_data, project_id, body)


@router.post("/related/insert")
def insert_related(project_id: str, body: RelatedDataImportRequest, response: Response) -> dict:
    return operation(response, insert_related_data, project_id, body)
