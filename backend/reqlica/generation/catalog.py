"""Short-lived public model metadata, never credentials or project content."""

import json
from threading import Lock
from time import monotonic

import httpx
from reqlica.generation.errors import GenerationError

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_catalog = {}
_cached_at = 0.0
_lock = Lock()


def openrouter_model(model):
    global _catalog, _cached_at
    with _lock:
        if monotonic() - _cached_at > 60 or not _catalog:
            try:
                with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                    with client.stream("GET", OPENROUTER_BASE + "/models") as response:
                        response.raise_for_status()
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 8 * 1024 * 1024:
                                raise ValueError("Catalog too large")
                            chunks.append(chunk)
                    models = json.loads(b"".join(chunks))["data"]
                    if not isinstance(models, list):
                        raise ValueError("Invalid catalog")
                    _catalog = {m["id"]: {"reasoning": m.get("reasoning"), "supported_parameters": m.get("supported_parameters", [])} for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)}
                    _cached_at = monotonic()
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise GenerationError("Could not load OpenRouter model capabilities. Retry the support check later.", 503) from None
        return _catalog.get(model)
