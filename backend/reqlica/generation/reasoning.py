"""Provider-reported reasoning controls, without model-name capability guesses."""

from reqlica.generation.errors import GenerationError
from reqlica.generation.catalog import OPENROUTER_BASE, openrouter_model

UNSUPPORTED_MODE_MESSAGE = "The selected reasoning mode is not supported by this model. Check model options in Settings."
MODES = ("default", "off", "minimal", "low", "medium", "high", "xhigh", "max")


def _openrouter_model(model):
    metadata = openrouter_model(model)
    return metadata.get("reasoning") if metadata else None


def reasoning_options(provider, model, base_url=None):
    modes = ["default"]
    message = "The provider does not report reasoning controls for this model. Provider default is available; this does not mean reasoning is unsupported."
    if provider == "compatible" and base_url == OPENROUTER_BASE:
        metadata = _openrouter_model(model)
        if isinstance(metadata, dict):
            if metadata.get("mandatory") is False:
                modes.append("off")
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
    value = {"enabled": False} if mode == "off" else {"effort": mode}
    return {"extra_body": {"reasoning": value}}
