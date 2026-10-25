"""Unified Project AI conversations and provider settings."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import SecretStr
from reqlica.generation import agent
from reqlica.generation.specifications import Specification
from reqlica.console.dependencies import draft_operation
from reqlica.generation.progress import inspect as inspect_activity, track
from reqlica.generation.errors import GenerationError
from reqlica.generation.schemas import ProviderEndpoint, ModelSettings, AgentRequest, AgentReplyRequest, AgentReasoningResetRequest
from reqlica.generation.reasoning import reasoning_options
from reqlica.generation.catalog import discover_models

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Generation"])
provider_router = APIRouter(prefix="/api/ai", tags=["Generation"])


@provider_router.post("/models")
def provider_models(body: ProviderEndpoint, response: Response,
                    api_key: Annotated[str | None, Header(alias="X-Reqlica-Provider-Key")] = None) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return discover_models(body, SecretStr(api_key) if api_key else None)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail, headers={"Cache-Control": "no-store"}) from error


@provider_router.post("/reasoning-options")
def model_reasoning_options(body: ModelSettings, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = reasoning_options(body.provider, body.model, body.base_url)
        return result
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail, headers={"Cache-Control": "no-store"}) from error

ActivityID = Annotated[str | None, Header(alias="X-Reqlica-Activity-ID", pattern=r"^[a-f0-9]{32}$")]


@router.get("/agent-runs")
def agent_runs(project_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(agent.list_runs, project_id)


@router.get("/agent-runs/{run_id}")
def agent_run(project_id: str, run_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(agent.inspect_run, project_id, run_id)


@router.post("/agent-runs/{run_id}/recover")
def recover_agent_run(project_id: str, run_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(agent.recover_run, project_id, run_id)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.put("/agent-runs/{run_id}")
def create_agent_run(project_id: str, run_id: str, body: AgentRequest, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(agent.create_run, project_id, run_id, body)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.get("/agent-runs/{run_id}/context")
async def agent_context(project_id: str, run_id: str, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await agent.inspect_context(request.app, project_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/agent-runs/{run_id}/step")
async def step_agent_run(
    project_id: str, run_id: str, request: Request, response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
    activity_id: ActivityID = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        with track(project_id, activity_id):
            return await agent.step_run(request.app, project_id, run_id,
                                        SecretStr(groq_key) if groq_key else None)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/agent-runs/{run_id}/replies")
def reply_agent_run(project_id: str, run_id: str, body: AgentReplyRequest, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(agent.reply_run, project_id, run_id, body)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.post("/agent-runs/{run_id}/reasoning-default")
def reset_agent_reasoning(project_id: str, run_id: str, body: AgentReasoningResetRequest, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(agent.reset_reasoning, project_id, run_id, body)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.get("/activity/{activity_id}")
def activity(project_id: str, activity_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(inspect_activity, project_id, activity_id)


@router.post("/specifications/preview")
def preview_specification(project_id: str, body: Specification, response: Response):
    from reqlica.projects.store import get_project
    from reqlica.generation.specifications import prepare
    response.headers["Cache-Control"] = "no-store"
    draft_operation(get_project, project_id)
    result = draft_operation(prepare, body, require_ready=False)
    return {key: value for key, value in result.items() if key != "requirements"}


@router.get("/schema-export")
def export_schema(project_id: str,
                  revision: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
                  format: Literal["json", "yaml"] = "json",
                  draft_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None):
    import json
    import yaml
    from reqlica.projects.schemas import get_api_schema, serving_document
    result = draft_operation(get_api_schema, project_id, draft_id)
    if revision != result["revision"]:
        raise HTTPException(409, "Schema changed; refresh the contract before downloading")
    from reqlica.cli import _console_base_url, _mock_base_url
    base = (f"{_console_base_url()}/api/projects/{project_id}/drafts/{draft_id}/preview" if draft_id
            else f"{_mock_base_url()}/mock/{project_id}")
    document = serving_document(result, base, preview=bool(draft_id))
    content = (json.dumps(document, ensure_ascii=False, indent=2) + "\n" if format == "json"
               else yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    filename = f"openapi-{draft_id or project_id}-{revision[:12]}.{format}"
    return Response(content, media_type="application/json" if format == "json" else "application/yaml",
                    headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Reqlica-Revision": revision})
