"""AI generation, read-only project chat and checkpointed MCP builds."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import SecretStr
from reqlica.generation import agent, jobs
from reqlica.console.dependencies import activity_operation, draft_operation
from reqlica.generation.progress import inspect as inspect_activity, track
from reqlica.generation.clarification import review_requirements
from reqlica.generation.errors import GenerationError
from reqlica.generation.questions import answer_project_question
from reqlica.generation.repair import repair_draft
from reqlica.generation.schemas import (
    ClarificationRequest,
    EndpointEditRequest,
    GenerationRequest,
    ProjectQuestionRequest,
    RepairRequest,
    ModelSettings,
    AgentRequest,
    AgentReplyRequest,
)
from reqlica.generation.reasoning import reasoning_options
from reqlica.generation.output_format import output_mode
from reqlica.generation.service import (
    edit_endpoint,
    endpoint_source,
    generate_endpoints,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Generation"])
provider_router = APIRouter(prefix="/api/ai", tags=["Generation"])


@provider_router.post("/reasoning-options")
def model_reasoning_options(body: ModelSettings, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = reasoning_options(body.provider, body.model, body.base_url)
        result["output_mode"] = output_mode(body)
        label = {"json_schema": "strict JSON schema", "json_object": "JSON mode", "text": "prompt-guided JSON (format controls not advertised)"}[result["output_mode"]]
        result["message"] += " Output format: " + label + "."
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


@router.put("/agent-runs/{run_id}")
def create_agent_run(project_id: str, run_id: str, body: AgentRequest, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(agent.create_run, project_id, run_id, body)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


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


@router.get("/generation-jobs")
def generation_jobs(project_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(jobs.list_jobs, project_id)


@router.get("/generation-jobs/{job_id}")
def generation_job(project_id: str, job_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(jobs.inspect_job, project_id, job_id)


@router.put("/generation-jobs/{job_id}")
def create_generation_job(project_id: str, job_id: str, body: GenerationRequest, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(jobs.create_job, project_id, job_id, body)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.post("/generation-jobs/{job_id}/step")
def step_generation_job(
    project_id: str, job_id: str, response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
    activity_id: ActivityID = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return activity_operation(project_id, activity_id, jobs.step_job, project_id, job_id,
                                  SecretStr(groq_key) if groq_key else None)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.post("/generation-jobs/{job_id}/resume")
def resume_generation_job(project_id: str, job_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return draft_operation(jobs.resume_job, project_id, job_id)
    except GenerationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.get("/activity/{activity_id}")
def activity(project_id: str, activity_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(inspect_activity, project_id, activity_id)


@router.post("/drafts/{draft_id}/repair")
def repair(
    project_id: str,
    draft_id: str,
    body: RepairRequest,
    response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
    activity_id: ActivityID = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return activity_operation(project_id, activity_id,
            repair_draft,
            project_id,
            draft_id,
            body,
            SecretStr(groq_key) if groq_key else None,
        )
    except GenerationError as error:
        raise HTTPException(
            error.status_code, error.detail, headers={"Cache-Control": "no-store"}
        ) from error


@router.post("/clarify")
def clarify(
    project_id: str,
    body: ClarificationRequest,
    response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
    activity_id: ActivityID = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return activity_operation(project_id, activity_id,
            review_requirements,
            project_id,
            body,
            SecretStr(groq_key) if groq_key else None,
        )
    except GenerationError as error:
        raise HTTPException(
            error.status_code, error.detail, headers={"Cache-Control": "no-store"}
        ) from error


@router.get("/endpoint-source")
def source(project_id: str, method: str, path: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(endpoint_source, project_id, method, path)


@router.post("/edit-endpoint", status_code=201)
def edit(
    project_id: str,
    body: EndpointEditRequest,
    response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
    activity_id: ActivityID = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = activity_operation(project_id, activity_id,
            edit_endpoint, project_id, body, SecretStr(groq_key) if groq_key else None
        )
        if result["status"] == "needs_information":
            response.status_code = 200
        return result
    except GenerationError as error:
        raise HTTPException(
            error.status_code, error.detail, headers={"Cache-Control": "no-store"}
        ) from error


@router.post("/ask")
def ask(
    project_id: str,
    body: ProjectQuestionRequest,
    response: Response,
    groq_key: Annotated[str | None, Header(alias="X-Reqlica-Groq-Key")] = None,
    activity_id: ActivityID = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return activity_operation(project_id, activity_id,
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
    activity_id: ActivityID = None,
) -> dict:
    # Credentials are request headers, not model input, project files, or stored
    # request models. Provider errors expose only sanitized application messages.
    response.headers["Cache-Control"] = "no-store"
    try:
        result = activity_operation(project_id, activity_id,
            generate_endpoints,
            project_id,
            body,
            SecretStr(groq_key) if groq_key else None,
        )
        if result["status"] == "needs_information":
            response.status_code = 200
        return result
    except GenerationError as error:
        raise HTTPException(
            error.status_code, error.detail, headers={"Cache-Control": "no-store"}
        ) from error
