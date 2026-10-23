"""Bounded provider model discovery. Only the public OpenRouter catalog is cached."""

import json
import os
from threading import Lock
from time import monotonic
from urllib.parse import quote

import httpx
from reqlica.generation.errors import GenerationError

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_BASE = "https://api.openai.com/v1"
OLLAMA_BASE = "http://localhost:11434"
_catalog = {}
_cached_at = 0.0
_lock = Lock()


def _model_metadata(item, ollama=False):
    if not isinstance(item, dict) or item.get("active") is False:
        return None
    model_id = item.get("model", item.get("name")) if ollama else item.get("id")
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
        return None
    result = {"id": model_id, "name": model_id}
    name = item.get("name")
    if isinstance(name, str) and len(name) <= 300:
        result["name"] = name
    parameters = item.get("supported_parameters")
    if isinstance(parameters, list):
        result["supported_parameters"] = [p for p in parameters[:100] if isinstance(p, str) and len(p) <= 100]
    reasoning = item.get("reasoning")
    if isinstance(reasoning, dict):
        result["reasoning"] = {}
        if isinstance(reasoning.get("mandatory"), bool):
            result["reasoning"]["mandatory"] = reasoning["mandatory"]
        efforts = reasoning.get("supported_efforts")
        if isinstance(efforts, list):
            result["reasoning"]["supported_efforts"] = [e for e in efforts[:20] if isinstance(e, str) and len(e) <= 20]
    return result


def _fetch_json(url, headers=None):
    try:
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            with client.stream("GET", url, headers=headers or {}) as response:
                if response.status_code in {401, 403}:
                    raise GenerationError("Model discovery was denied. Check the API key and model-list permissions for this provider.", 401)
                if response.status_code in {301, 302, 303, 307, 308}:
                    raise GenerationError("Model discovery does not follow redirects. Enter the provider's final API base URL.", 502)
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise ValueError("Catalog too large")
                    chunks.append(chunk)
        return json.loads(b"".join(chunks))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise GenerationError("Could not load the provider's model metadata. Check the base URL and connection, or enter a model identifier manually.", 503) from None


def _fetch_models(base_url, key="", *, ollama=False):
    headers = {"Authorization": "Bearer " + key} if key else {}
    payload = _fetch_json(base_url + ("/api/tags" if ollama else "/models"), headers)
    models = payload.get("models" if ollama else "data") if isinstance(payload, dict) else None
    if not isinstance(models, list) or len(models) > 10000:
        raise GenerationError("The provider returned an invalid model list. Enter a model identifier manually or retry discovery.", 503)
    result = {}
    for item in models:
        metadata = _model_metadata(item, ollama)
        if metadata:
            result[metadata["id"]] = metadata
    return sorted(result.values(), key=lambda m: m["id"].casefold())


def openrouter_availability(model):
    """Best-effort diagnosis after a 404, never an inference gate or model fallback."""
    parts = model.split("/", 1)
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        return None
    slug = "/".join(quote(part, safe="") for part in parts)
    try:
        payload = _fetch_json(OPENROUTER_BASE + "/models/" + slug + "/endpoints")
    except GenerationError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or data.get("id") != model or not isinstance(data.get("endpoints"), list):
        return None
    return {"endpoint_count": len(data["endpoints"])}


def openrouter_models(*, refresh=False):
    global _catalog, _cached_at
    with _lock:
        if refresh or monotonic() - _cached_at > 60 or not _catalog:
            models = _fetch_models(OPENROUTER_BASE)
            _catalog = {m["id"]: m for m in models}
            _cached_at = monotonic()
        return list(_catalog.values())


def openrouter_model(model):
    return next((m for m in openrouter_models() if m["id"] == model), None)


def discover_models(settings, api_key=None):
    """Never reuse server credentials for a custom destination or cache private lists."""
    base = settings.base_url
    key = api_key.get_secret_value().strip() if api_key else ""
    if settings.provider == "groq":
        base = GROQ_BASE_URL
        key = key or os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API") or ""
        if not key:
            raise GenerationError("Enter a Groq API key, or configure the server's GROQ_API_KEY, before fetching models.", 422)
    elif settings.provider == "openai":
        base = OPENAI_BASE
    elif settings.provider == "ollama":
        base = base or OLLAMA_BASE
    if settings.provider == "compatible" and base == OPENROUTER_BASE:
        models = openrouter_models(refresh=True)
        message = "Models from OpenRouter's public catalog. Account access and tool execution have not been tested."
    else:
        models = _fetch_models(base, key, ollama=settings.provider == "ollama")
        message = "Models reported by this provider. Listing does not verify chat, tools, or reasoning support."
    return {"models": models, "message": message}
