"""Stateless Groq requests: read-only explanations or unexecuted endpoint drafts."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import SecretStr
from reqlica.console.dependencies import draft_operation
from reqlica.generation.errors import GenerationError
from reqlica.generation.questions import answer_project_question
from reqlica.generation.schemas import GenerationRequest, ProjectQuestionRequest
from reqlica.generation.service import generate_endpoints

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Generation"])


@router.post("/ask")
def ask(
    project_id: str,
    body: ProjectQuestionRequest,
    response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(
            answer_project_question,
            project_id,
            body,
            SecretStr(groq_key) if groq_key else None,
        )
    except GenerationError as error:
        raise HTTPException(
            error.status_code, error.detail, headers={"Cache-Control": "no-store"}
        ) from error


@router.post("/generate", status_code=201)
def generate(
    project_id: str,
    body: GenerationRequest,
    response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
) -> dict:
    # Credentials are request headers, not model input, project files, or stored
    # request models. Provider errors expose only sanitized application messages.
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(
            generate_endpoints,
            project_id,
            body,
            SecretStr(groq_key) if groq_key else None,
        )
    except GenerationError as error:
        raise HTTPException(
            error.status_code, error.detail, headers={"Cache-Control": "no-store"}
        ) from error
