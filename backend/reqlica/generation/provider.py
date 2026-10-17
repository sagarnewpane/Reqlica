"""One Groq chat-completion request, with no automatic retries or import effects."""

import os

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, SecretStr
from reqlica.generation.errors import GenerationError
from reqlica.generation.schemas import GeneratedPlan, ModelSettings, ProjectAnswer

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
PROVIDER_TIMEOUT = 55.0
MAX_RESPONSE_BYTES = 256 * 1024


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
            "Try a smaller request or select openai/gpt-oss-120b"
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
    readonly = response_model is ProjectAnswer
    operation = "project question" if readonly else "generation"
    output = "answer" if readonly else "proposal"
    unchanged = "no project changes were made" if readonly else "no draft was saved"
    key = api_key.get_secret_value().strip() if api_key else ""
    key = key or os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API")
    if not key:
        raise GenerationError(
            "Configure GROQ_API_KEY (or GROQ_API) on the console, or supply a Groq key for this request",
            503,
        )
    try:
        # A fixed origin prevents a browser-supplied URL from turning the console
        # into an arbitrary proxy or forwarding a server credential elsewhere.
        with (
            httpx.Client(trust_env=False, follow_redirects=False) as transport,
            OpenAI(
                api_key=key,
                base_url=GROQ_BASE_URL,
                http_client=transport,
                timeout=PROVIDER_TIMEOUT,
                max_retries=0,
            ) as client,
        ):
            response = client.chat.completions.create(
                model=request.model,
                messages=messages,
                temperature=request.temperature,
                max_completion_tokens=request.max_tokens,
                reasoning_effort="low",
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "reqlica_endpoint_proposal"
                        if response_model is GeneratedPlan
                        else "reqlica_project_answer",
                        "strict": True,
                        "schema": response_model.model_json_schema(),
                    },
                },
            )
    except APITimeoutError as error:
        raise GenerationError(
            f"Groq timed out; {unchanged} and no automatic retry was made", 504
        ) from error
    except APIStatusError as error:
        if error.status_code in {401, 403}:
            message = "Groq rejected the credentials or model access"
        elif error.status_code == 429:
            message = "Groq rate limit reached; try later (no automatic retry)"
        elif error.status_code == 400:
            reason = _bad_request_reason(error)
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
        raise GenerationError(
            message, 503 if error.status_code == 429 else 502
        ) from error
    except APIConnectionError as error:
        raise GenerationError(f"Could not connect to Groq; {unchanged}") from error
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise GenerationError(f"Groq returned no {operation} choices")
    choice = choices[0]
    if getattr(choice, "finish_reason", None) != "stop":
        raise GenerationError(
            f"Groq did not finish the {output}; increase the output token limit or simplify the request"
        )
    message = getattr(choice, "message", None)
    text = getattr(message, "content", None)
    if getattr(message, "refusal", None) or not isinstance(text, str) or not text:
        raise GenerationError(f"Groq refused the request or returned no {output}")
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise GenerationError(f"Groq {output} exceeds the 256 KiB output limit")
    return text, response.usage.model_dump() if response.usage else None
