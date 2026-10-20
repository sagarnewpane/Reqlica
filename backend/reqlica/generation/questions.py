"""Read-only answers grounded in fresh context and bounded client-held history."""

from pydantic import SecretStr
from reqlica.generation import provider
from reqlica.generation.errors import GenerationError
from reqlica.generation.json_output import parse_json
from reqlica.generation.prompts import build_question_messages
from reqlica.generation.schemas import ProjectAnswer, ProjectQuestionRequest


def answer_project_question(
    project_id: str, request: ProjectQuestionRequest, api_key: SecretStr | None = None
) -> dict:
    context, messages = build_question_messages(project_id, request)
    text, usage = provider.complete(
        messages, request, api_key, response_model=ProjectAnswer
    )
    try:
        answer = ProjectAnswer.model_validate(parse_json(text))
        if not answer.answer.strip() or len(answer.answer) > 32_768:
            raise ValueError("Answer length")
        if len(answer.sources) > 50 or len(answer.limitations) > 20:
            raise ValueError("Answer item count")
        if any(len(item) > 4096 for item in answer.limitations):
            raise ValueError("Limitation length")
        allowed_sources = {
            path for path in context["source"] if path.endswith(".py")
        } | {"database_definitions", "sdk", "environment"}
        if context["manifest_error"] is None:
            allowed_sources.add("manifest.json")
        if context["project_notes"]["status"] == "present":
            allowed_sources.add("PROJECT_CONTEXT.md")
        if context["database_setup"]["status"] == "present":
            allowed_sources.update({"database_setup", "database/schema.json"})
        if not set(answer.sources) <= allowed_sources:
            raise ValueError("Unknown source references")
    except (ValueError, RecursionError) as error:
        # Schema errors may contain model-produced source or private text.
        raise GenerationError(
            "Groq returned an invalid project answer or unsupported source references; no project changes were made"
        ) from error
    return {
        "status": "answered",
        **answer.model_dump(),
        "sources": list(dict.fromkeys(answer.sources)),
        "context_revision": context["revision"],
        "history_used": len(request.history),
        "provider": {"kind": request.provider, "model": request.model, "usage": usage},
        "warning": "Based on the published snapshot, not verified runtime behavior. Prior conversation is reference only. No code was run or changed; no draft was created.",
    }
