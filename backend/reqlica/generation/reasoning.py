"""Discover supported reasoning controls and translate explicit choices safely."""

import re
from reqlica.generation.errors import GenerationError
from reqlica.generation.catalog import OPENROUTER_BASE, openrouter_model

MODES = ("default", "off", "minimal", "low", "medium", "high", "xhigh", "max")


def _openrouter_model(model):
    metadata = openrouter_model(model)
    return metadata.get("reasoning") if metadata else None


def reasoning_options(provider, model, base_url=None):
    modes = ["default"]
    message = "Model support is unknown. Only Provider default is available."
    if provider == "groq":
        modes += ["low", "medium", "high"]
        message = "GPT-OSS requires reasoning. Provider default keeps Reqlica's low effort setting."
    elif provider == "compatible" and base_url == OPENROUTER_BASE:
        metadata = _openrouter_model(model)
        if isinstance(metadata, dict):
            if metadata.get("mandatory") is False:
                modes.append("off")
            efforts = metadata.get("supported_efforts")
            if isinstance(efforts, list):
                modes += [value for value in MODES[2:] if value in efforts]
            message = "Reasoning is required by this model." if metadata.get("mandatory") is True else "Options reported by OpenRouter for this model."
    elif provider == "openai":
        # Conservative, documented Chat Completions support. Unknown models keep defaults.
        # https://developers.openai.com/api/docs/models/gpt-5.1
        # https://developers.openai.com/api/docs/models/gpt-5.2
        if re.fullmatch(r"gpt-5\.1(?:-\d{4}-\d{2}-\d{2})?", model):
            modes += ["off", "low", "medium", "high"]
        elif re.fullmatch(r"gpt-5\.2(?:-\d{4}-\d{2}-\d{2})?", model):
            modes += ["off", "low", "medium", "high", "xhigh"]
        elif re.fullmatch(r"(?:gpt-5(?:-mini|-nano)?|o3(?:-mini)?|o4-mini)(?:-\d{4}-\d{2}-\d{2})?", model):
            modes += ["low", "medium", "high"]
        if len(modes) > 1:
            message = "Documented OpenAI model options. Off disables reasoning only on supported models."
    return {"modes": modes, "message": message}


def reasoning_parameters(request):
    mode = request.reasoning_mode
    if mode == "default":
        return {"reasoning_effort": "low"} if request.provider == "groq" else {}
    options = reasoning_options(request.provider, request.model, request.base_url)
    if mode not in options["modes"]:
        raise GenerationError("The selected reasoning mode is not supported by this model. Check model options in Settings.", 422)
    if request.provider == "compatible":
        value = {"enabled": False} if mode == "off" else {"effort": mode}
        return {"extra_body": {"reasoning": value}}
    return {"reasoning_effort": "none" if mode == "off" else mode}
