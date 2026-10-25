"""Provider-reported controls and documented controls for exact provider/model pairs."""

from reqlica.generation.errors import GenerationError
from reqlica.generation.catalog import OPENROUTER_BASE, openrouter_model

UNSUPPORTED_MODE_MESSAGE = "The selected reasoning mode is not supported by this model. Check model options in Settings."
MODES = ("default", "off", "minimal", "low", "medium", "high", "xhigh", "max")
CEREBRAS_BASE = "https://api.cerebras.ai/v1"
# Cerebras Chat Completions docs: reasoning_effort for qwen-3.8-27b.
# Keep exact IDs: other Qwen versions and compatible endpoints may differ.
CEREBRAS_MODES = {"qwen-3.8-27b": ["default", "off", "low", "medium", "high"]}


def _openrouter_model(model):
    metadata = openrouter_model(model)
    return metadata.get("reasoning") if metadata else None


def reasoning_options(provider, model, base_url=None):
    modes = ["default", "off"]
    message = "Off is the app default. This provider does not report a verified reasoning disable control for this model, so reasoning may still run."
    if provider == "compatible" and base_url == CEREBRAS_BASE and model in CEREBRAS_MODES:
        return {"modes": CEREBRAS_MODES[model], "message": "Cerebras supports Off, Low, Medium and High for this model. Provider default is High; Off disables reasoning."}
    if provider == "compatible" and base_url == OPENROUTER_BASE:
        metadata = _openrouter_model(model)
        if isinstance(metadata, dict):
            if metadata.get("mandatory") is True:
                modes.remove("off")
            efforts = metadata.get("supported_efforts")
            if isinstance(efforts, list):
                modes += [value for value in MODES[2:] if value in efforts]
            message = "Reasoning is required by this model." if metadata.get("mandatory") is True else "Options reported by OpenRouter for this model."
    return {"modes": modes, "message": message}


def reasoning_parameters(request):
    mode = request.reasoning_mode
    if mode == "default":
        return {}
    options = reasoning_options(request.provider, request.model, request.base_url)
    if mode not in options["modes"]:
        raise GenerationError(f"Reasoning {mode!r} is unavailable for this model. Available: {', '.join(options['modes'])}. Use Provider default, or choose a supported mode in Settings.", 422, code="unsupported_reasoning_mode", reasoning_mode=mode, supported_modes=options["modes"])
    if request.provider == "compatible" and request.base_url == CEREBRAS_BASE:
        if request.model in CEREBRAS_MODES:
            return {"reasoning_effort": "none" if mode == "off" else mode}
    if mode == "off" and not (request.provider == "compatible" and request.base_url == OPENROUTER_BASE):
        # Off is a preference across all connections. Never send unverified
        # control parameters to arbitrary APIs or non-reasoning models.
        return {}
    value = {"enabled": False} if mode == "off" else {"effort": mode}
    return {"extra_body": {"reasoning": value}}
