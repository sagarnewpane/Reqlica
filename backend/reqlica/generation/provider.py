"""One provider chat-completion request, with no automatic retries or import effects."""

import json
import os
import re
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import SecretStr
from reqlica.generation.errors import GenerationError
from reqlica.generation.progress import report
from reqlica.generation.reasoning import reasoning_parameters
from reqlica.generation.catalog import GROQ_BASE_URL, OPENROUTER_BASE, openrouter_model, openrouter_availability
from reqlica.generation.schemas import ModelSettings

MAX_RESPONSE_BYTES = 512 * 1024
# Best-effort reproducibility, not a promise of identical provider output.
SAMPLING_SEED = 42


def _usage(response):
    """Keep reported counters, never arbitrary provider response fields."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if type(value) is int and 0 <= value < 10**12:
            result[key] = value
    for parent, key in (("completion_tokens_details", "reasoning_tokens"), ("prompt_tokens_details", "cached_tokens")):
        value = getattr(getattr(usage, parent, None), key, None)
        if type(value) is int and 0 <= value < 10**12:
            result[parent] = {key: value}
    return result or None


def _tool_parameters(request, base_url, tools):
    parameters = {"tools": tools}
    # Custom compatible endpoints need not implement every OpenAI option. Tool
    # calls are serialized by the agent even if a model returns a batch.
    parallel = request.provider in {"groq", "openai"}
    if parallel:
        parameters["tool_choice"] = "auto"
    if request.provider == "compatible" and base_url == OPENROUTER_BASE:
        try:
            metadata = openrouter_model(request.model)
        except GenerationError:
            # Public discovery is advisory; a catalog outage must not block a
            # manually configured model. The actual inference API decides access.
            metadata = None
        supported = metadata.get("supported_parameters") if metadata else None
        if isinstance(supported, list):
            if "tools" not in supported:
                raise GenerationError(
                    "The selected OpenRouter model does not advertise function tools. "
                    "Choose a tool-capable model for Project AI.",
                    422, code="tools_unsupported",
                )
            parallel = "parallel_tool_calls" in supported
            if "tool_choice" in supported:
                parameters["tool_choice"] = "auto"
    if parallel:
        parameters["parallel_tool_calls"] = False
    return parameters


def _not_found_reason(error, openrouter, model):
    """Classify known 404 reasons without echoing the provider's raw response."""
    body = error.body
    detail = body.get("error", body) if isinstance(body, dict) else {}
    message = detail.get("message", "") if isinstance(detail, dict) else ""
    message = message.lower() if isinstance(message, str) else ""
    if openrouter and "no endpoints" in message:
        if any(value in message for value in ("data policy", "data policies", "privacy", "data collection", "zdr")):
            return "No OpenRouter endpoint matches your account's data/privacy policy. Review the provider settings in OpenRouter."
        if "support" in message and any(value in message for value in ("parameter", "tool")):
            return "No OpenRouter endpoint supports all requested parameters for this model. Check tool support and reasoning settings."
    if openrouter:
        availability = openrouter_availability(model)
        if availability is not None and availability["endpoint_count"] == 0:
            return "OpenRouter currently reports no provider endpoints for this exact model ID. Check the model's current provider availability on OpenRouter."
        return "OpenRouter returned HTTP 404 for this request. Check the model's provider availability, account routing policies, and requested parameters; the public model list alone does not establish availability."
    return "The provider could not find the selected model or Chat Completions endpoint. Check the model identifier and API base URL in Settings. Saved conversations keep their original provider settings; use an updated profile in a new conversation."






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
            "Groq rejected the selected model. Select an available tool-capable model "
            "and check model access for your Groq project"
        )
    if param in ("response_format", "reasoning_effort", "temperature"):
        return (
            "Groq rejected a generation setting. "
            "Check the selected model's support for structured output and reasoning settings"
        )
    return ""


def _request_size_reason(error):
    """Extract only recognized quota labels and numbers, never raw provider text."""
    body = error.body
    detail = body.get("error", body) if isinstance(body, dict) else {}
    text = detail.get("message") if isinstance(detail, dict) else None
    text = text[:8192].lower() if isinstance(text, str) else ""
    metric = None
    for name, pattern in (
        ("input_tokens_per_minute", r"input tokens per minute|\bitpm\b"),
        ("output_tokens_per_minute", r"output tokens per minute|\botpm\b"),
        ("tokens_per_minute", r"tokens per minute|\btpm\b"),
        ("tokens_per_day", r"tokens per day|\btpd\b"),
    ):
        if re.search(pattern, text):
            metric = name
            break
    if metric:
        quota = {"metric": metric}
        for name, pattern in (
            ("limit", r"\blimit\s*[:=]?\s*((?:\d{1,3}(?:,\d{3})+)|\d+)(?!\w|,\d|\.\d)"),
            ("requested", r"\brequested\s*[:=]?\s*((?:\d{1,3}(?:,\d{3})+)|\d+)(?!\w|,\d|\.\d)"),
        ):
            match = re.search(pattern, text)
            if match:
                digits = match.group(1).replace(",", "")
                if len(digits) <= 12:
                    quota[name] = int(digits)
        label = metric.replace("_", " ")
        message = f"The provider reports a {label} allowance"
        if "limit" in quota:
            message += f" of {quota['limit']:,}"
        if "requested" in quota:
            message += f"; this request was counted as {quota['requested']:,} tokens"
        message += ". "
        if quota.get("requested", 0) > quota.get("limit", float("inf")):
            message += "This single request exceeds that allowance; waiting alone will not make it fit. "
        message += "Use less input/history, a smaller output budget, or an account with a larger allowance."
        return message, quota
    return (
        "The provider rejected the request size but did not report a recognized token quota. "
        "Reduce input/history or check the provider's model and account limits. "
        "Lowering the output budget alone may not resolve an oversized input.",
        None,
    )


def complete(
    messages: list[dict],
    request: ModelSettings,
    api_key: SecretStr | None = None,
    *,
    tools: list[dict],
) -> tuple[dict, dict | None]:
    operation, output = "Project AI", "answer"
    unchanged = "any earlier draft changes are retained"
    groq = request.provider == "groq"
    label = "Groq" if groq else "Ollama" if request.provider == "ollama" else "OpenAI" if request.provider == "openai" else "OpenAI-compatible provider"
    base_url = GROQ_BASE_URL if groq else request.base_url or "https://api.openai.com/v1"
    if request.provider == "ollama":
        base_url = (request.base_url or "http://localhost:11434") + "/v1"

    def failure(message, *args, **details):
        return GenerationError(message.replace("Groq", label), *args, **details)

    key = api_key.get_secret_value().strip() if api_key else ""
    # Server Groq credentials must NEVER be forwarded to a configured URL.
    if groq:
        key = key or os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API")
    elif request.provider == "ollama" and urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        # The SDK requires a value; local Ollama ignores authentication. Remote
        # servers still require an explicit credential, never a server Groq key.
        key = key or "ollama"
    if not key:
        raise failure(
            "Configure GROQ_API_KEY (or GROQ_API) on the console, or supply a Groq key for this request"
            if groq else "Enter an API key for the selected provider in Settings (stored in this browser)",
            503,
        )
    reasoning = reasoning_parameters(request)
    extra_body = dict(reasoning.pop("extra_body", {}))
    if request.provider == "compatible" and base_url == OPENROUTER_BASE and request.reasoning_mode != "default":
        # An explicit effort/off choice must not silently be ignored. Ordinary
        # tool calls use OpenRouter's default routing without an extra filter.
        extra_body["provider"] = {"require_parameters": True}
    if extra_body:
        reasoning["extra_body"] = extra_body
    tool_parameters = _tool_parameters(request, base_url, tools)
    wire_messages = messages
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
                   {"max_completion_tokens" if request.provider == "openai" else "max_tokens": request.max_tokens}),
                **reasoning,
                **tool_parameters,
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
            "tools_bytes": len(json.dumps(tools, ensure_ascii=False).encode("utf-8")),
        }
        if error.status_code in {401, 403}:
            message = "Groq rejected the credentials or model access"
        elif error.status_code == 429:
            message = "Groq rate limit reached; try later (no automatic retry)"
        elif error.status_code == 404:
            message = (
                f"Groq rejected the {operation} request (HTTP 404): "
                + _not_found_reason(error, request.provider == "compatible" and base_url == OPENROUTER_BASE, request.model)
                + f" {unchanged.capitalize()}; no automatic retry was made."
            )
        elif error.status_code == 413:
            reason, quota = _request_size_reason(error)
            if quota:
                diagnostics["provider_quota"] = quota
            message = (
                f"Groq rejected the {operation} request as too large (HTTP 413). "
                + reason + " Settings changes apply to new conversations; this conversation retains its saved output budget. "
                + f"{unchanged.capitalize()} and no automatic retry was made"
            )
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
        usage = _usage(response)
        counts = (
            f" Provider reported {usage.get('prompt_tokens', 'unknown')} input tokens and "
            f"{usage.get('completion_tokens', 'unknown')} generated tokens (including any reported reasoning)."
            if usage else " The provider did not report token usage for this response."
        )
        raise failure(
            f"Groq stopped before finishing the {output} (finish_reason=length). "
            f"The configured output cap is {request.max_tokens:,} tokens; this does not prove that many were generated."
            + counts + " Check the runtime context window as well as the output budget; " + unchanged,
            code="output_token_limit",
            diagnostics={"finish_reason": "length", "max_output_tokens": request.max_tokens, "usage": usage},
        )
    if getattr(choice, "finish_reason", None) not in {"stop", "tool_calls"}:
        raise failure(
            f"Groq stopped without completing the {output}; {unchanged}"
        )
    message = getattr(choice, "message", None)
    text = getattr(message, "content", None)
    if getattr(message, "refusal", None):
        raise failure("The model refused the AI build request; earlier draft changes are retained")
    payload = {"role": "assistant", "content": text}
    calls = getattr(message, "tool_calls", None)
    if calls:
        payload["tool_calls"] = [call.model_dump(include={"id", "type", "function"}) for call in calls]
    if (not text and not calls) or len(json.dumps(payload).encode()) > MAX_RESPONSE_BYTES:
        raise failure("The model returned an empty or oversized tool response; earlier draft changes are retained")
    return payload, _usage(response)
