"""One Groq chat-completion request, with no automatic retries or import effects."""

import json
import os

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, SecretStr, ValidationError
from reqlica.generation.data_schemas import MockDataPlan
from reqlica.generation.errors import GenerationError
from reqlica.generation.progress import report
from reqlica.generation.reasoning import reasoning_parameters
from reqlica.generation.output_format import output_mode, format_parameters, format_instructions
from reqlica.generation.catalog import OPENROUTER_BASE
from reqlica.generation.schemas import (
    ClarificationReview,
    GeneratedPlan,
    ModelSettings,
    ProjectAnswer,
    RepairPlan,
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MAX_RESPONSE_BYTES = 256 * 1024
# Best-effort reproducibility, not a promise of identical provider output.
SAMPLING_SEED = 42


def _schema_failures(error: APIStatusError, model: type[BaseModel]) -> list[dict]:
    """Expose only known field paths and fixed reasons, never generated values."""
    body = error.body
    detail = body.get("error", body) if isinstance(body, dict) else None
    raw = detail.get("failed_generation") if isinstance(detail, dict) else None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return []
    try:
        model.model_validate_json(raw)
    except ValidationError as failure:
        return validation_failures(failure, model)
    except (ValueError, RecursionError):
        return []
    return []


def validation_failures(failure: ValidationError, model: type[BaseModel]) -> list[dict]:
    """Bounded diagnostics shared by provider and local response validation."""
    fields = set()

    def collect(schema):
        if isinstance(schema, dict):
            fields.update(schema.get("properties", {}))
            for item in schema.values():
                collect(item)
        elif isinstance(schema, list):
            for item in schema:
                collect(item)

    collect(model.model_json_schema())
    reasons = {
        "model_type": "expected a JSON object",
        "dict_type": "expected a JSON object",
        "list_type": "expected an array",
        "literal_error": "expected one of the allowed values",
        "int_type": "expected an integer",
        "bool_type": "expected a boolean",
        "value_error": "value fails an application constraint",
        "string_type": "expected a string",
        "string_pattern_mismatch": "string does not match the required pattern",
        "string_too_long": "string exceeds the length limit",
        "string_too_short": "string is too short",
        "too_long": "too many items",
        "missing": "required field is missing",
        "extra_forbidden": "unexpected field",
        "json_invalid": "invalid JSON",
    }
    return [
        {
            "type": issue["type"],
            "path": ".".join(
                str(part) if isinstance(part, int) or part in fields else "<field>"
                for part in issue["loc"]
            ) or "<root>",
            "reason": reasons.get(issue["type"], "value violates the response schema"),
        }
        for issue in failure.errors(include_input=False, include_context=False)[:8]
    ]


def _bad_request_reason(error: APIStatusError) -> str:
    """Classify provider diagnostics, never echo their untrusted text or output.

    Groq can include generated source in ``failed_generation`` or even in the
    message. Use only fixed public explanations, not a redacted raw response.
    """
    body = error.body
    if not isinstance(body, dict):
        return ""
    detail = body.get("error", body)
    if not isinstance(detail, dict):
        return ""
    code = detail.get("code")
    message = detail.get("message")
    message = message.lower() if isinstance(message, str) else ""
    param = detail.get("param")
    if (
        code in ("json_validate_failed", "json_generation_failed")
        or ("generated json" in message and ("schema" in message or "valid" in message))
        or "failed to generate json" in message
    ):
        return (
            "Groq could not produce a response matching the required JSON schema. "
            "This is an output-schema failure; the provider did not report a context-limit error. "
            "See validation details when available; increasing the token limit is not an established fix"
        )
    if code in ("context_length_exceeded", "context_window_exceeded") or (
        "context" in message and ("length" in message or "window" in message)
    ):
        return (
            "The requirements, project context, and output token budget exceed "
            "Groq's context limit. Reduce the requirements, project size, or output token limit"
        )
    if "schema" in message and any(
        word in message for word in ("invalid", "unsupported", "not supported", "must")
    ):
        return (
            "Groq rejected the structured-output schema. "
            "This is a provider/schema compatibility issue, not a credentials error"
        )
    if param in ("max_tokens", "max_completion_tokens") or (
        any(word in message for word in ("max_tokens", "max_completion_tokens"))
        and any(word in message for word in ("limit", "must", "maximum", "range"))
    ):
        return (
            "Groq rejected the output token budget. Lower Max output tokens in Settings"
        )
    if code in ("model_not_found", "model_decommissioned") or param == "model":
        return (
            "Groq rejected the selected model. Select an available GPT-OSS model "
            "and check model access for your Groq project"
        )
    if param in ("response_format", "reasoning_effort", "temperature"):
        return (
            "Groq rejected a generation setting. "
            "Check the selected model's support for structured output and reasoning settings"
        )
    return ""


def complete(
    messages: list[dict],
    request: ModelSettings,
    api_key: SecretStr | None = None,
    *,
    response_model: type[BaseModel] = GeneratedPlan,
) -> tuple[str, dict | None]:
    clarification = response_model is ClarificationReview
    repair = response_model is RepairPlan
    readonly = response_model is ProjectAnswer or clarification
    data_only = response_model is MockDataPlan
    operation = (
        "repair"
        if repair
        else "clarification"
        if clarification
        else "mock data"
        if data_only
        else "project question"
        if readonly
        else "generation"
    )
    output = "records" if data_only else "answer" if readonly else "proposal"
    unchanged = (
        "no repair changes were saved"
        if repair
        else "no data was saved"
        if data_only
        else "no project changes were made"
        if readonly
        else "no draft was saved"
    )
    groq = request.provider == "groq"
    label = "Groq" if groq else "OpenAI" if request.provider == "openai" else "OpenAI-compatible provider"
    base_url = GROQ_BASE_URL if groq else request.base_url or "https://api.openai.com/v1"

    def failure(message, *args, **details):
        return GenerationError(message.replace("Groq", label), *args, **details)

    key = api_key.get_secret_value().strip() if api_key else ""
    # Server Groq credentials must NEVER be forwarded to a configured URL.
    if groq:
        key = key or os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API")
    if not key:
        raise failure(
            "Configure GROQ_API_KEY (or GROQ_API) on the console, or supply a Groq key for this request"
            if groq else "Enter an API key for the selected provider in Settings (held in page memory only)",
            503,
        )
    mode = output_mode(request)
    report("format_" + mode)
    reasoning = reasoning_parameters(request)
    extra_body = dict(reasoning.pop("extra_body", {}))
    if request.provider == "compatible" and base_url == OPENROUTER_BASE and (mode != "text" or request.reasoning_mode != "default"):
        extra_body["provider"] = {"require_parameters": True}
    if extra_body:
        reasoning["extra_body"] = extra_body
    name = ("reqlica_draft_repair" if repair else "reqlica_clarification_review" if clarification
            else "reqlica_mock_records" if data_only else "reqlica_project_answer" if readonly
            else "reqlica_endpoint_proposal")
    wire_messages = [*messages, {"role": "system", "content":
        format_instructions(response_model, include_schema=mode != "json_schema")}]
    try:
        # Custom destinations receive only their explicitly supplied key. Never
        # follow redirects or inherit environment proxies.
        with (
            httpx.Client(trust_env=False, follow_redirects=False, event_hooks={
                "response": [lambda response: report(
                    "provider_accepted" if response.is_success else "provider_rejected"
                )],
            }) as transport,
            OpenAI(
                api_key=key,
                base_url=base_url,
                http_client=transport,
                timeout=httpx.Timeout(request.timeout_seconds, connect=10, write=30, pool=10),
                max_retries=0,
            ) as client,
        ):
            report("provider_request")
            response = client.chat.completions.create(
                model=request.model,
                messages=wire_messages,
                **({} if request.provider == "openai" and request.reasoning_mode not in {"default", "off"} else {"temperature": request.temperature}),
                **({"seed": SAMPLING_SEED, "max_completion_tokens": request.max_tokens} if groq else
                   {"max_completion_tokens" if request.provider == "openai" and request.model.startswith(("gpt-5", "o1", "o3", "o4")) else "max_tokens": request.max_tokens}),
                **reasoning,
                **format_parameters(mode, name, response_model),
            )
    except APITimeoutError as error:
        raise failure(
            f"Groq network wait timed out (response wait limit: {request.timeout_seconds}s); "
            f"{unchanged} and no automatic retry was made", 504
        ) from error
    except APIStatusError as error:
        diagnostics = {
            "operation": operation,
            "model": request.model,
            "provider_status": error.status_code,
            "max_output_tokens": request.max_tokens,
            "prompt_bytes": len(json.dumps(wire_messages, ensure_ascii=False).encode("utf-8")),
        }
        if error.status_code in {401, 403}:
            message = "Groq rejected the credentials or model access"
        elif error.status_code == 429:
            message = "Groq rate limit reached; try later (no automatic retry)"
        elif error.status_code == 413:
            message = (
                f"Groq rejected the {operation} request as too large (HTTP 413). "
                "The request exceeds the provider's request-size or token allowance "
                "for this model/account. Lower Max output tokens in Settings; if it "
                "still fails, use a model/account with a larger allowance or reduce "
                "project context. Waiting alone may not resolve a per-request limit. "
                f"{unchanged.capitalize()} and no automatic retry was made"
            )
        elif error.status_code == 400:
            reason = _bad_request_reason(error)
            failures = (
                _schema_failures(error, response_model)
                if "output-schema failure" in reason
                else []
            )
            if failures:
                diagnostics["validation_errors"] = failures
                reason += ". " + "; ".join(
                    f"{issue['path']}: {issue['reason']}" for issue in failures[:3]
                )
            message = (
                f"Groq rejected the {operation} request (HTTP 400): {reason}"
                if reason
                else f"Groq rejected the {operation} request (HTTP 400); "
                "the provider did not return a recognized failure reason. "
                "Try a smaller request and check the selected model and output token limit"
            )
            message += f". {unchanged.capitalize()} and no automatic retry was made"
        else:
            message = (
                f"Groq rejected the {operation} request (HTTP {error.status_code})"
            )
        raise failure(
            message, 413 if error.status_code == 413 else 503 if error.status_code == 429 else 502,
            diagnostics=diagnostics,
        ) from error
    except APIConnectionError as error:
        raise failure(f"Could not connect to Groq; {unchanged}") from error
    report("provider_response")
    report("checking")
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise failure(f"Groq returned no {operation} choices")
    choice = choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise failure(
            f"Groq reached the {request.max_tokens}-token output limit before finishing the {output}. "
            f"Increase Max output tokens or reduce the requested scope; {unchanged}",
            code="output_token_limit",
        )
    if getattr(choice, "finish_reason", None) != "stop":
        raise failure(
            f"Groq stopped without completing the {output}; {unchanged}"
        )
    message = getattr(choice, "message", None)
    text = getattr(message, "content", None)
    if getattr(message, "refusal", None) or not isinstance(text, str) or not text:
        raise failure(f"Groq refused the request or returned no {output}")
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise failure(f"Groq {output} exceeds the 256 KiB output limit")
    return text, response.usage.model_dump() if response.usage else None
