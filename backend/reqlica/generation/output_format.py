"""Choose supported wire formats and keep prompt-only JSON requirements explicit."""

import json
from reqlica.generation.catalog import OPENROUTER_BASE, openrouter_model


def output_mode(request):
    if request.provider == "groq":
        return "json_schema"
    if request.provider == "compatible" and request.base_url == OPENROUTER_BASE:
        metadata = openrouter_model(request.model) or {}
        parameters = metadata.get("supported_parameters") or []
        if not isinstance(parameters, list):
            parameters = []
        if "structured_outputs" in parameters:
            return "json_schema"
        if "response_format" in parameters:
            return "json_object"
        return "text"
    # Always request strict output on direct APIs. Unsupported models/endpoints
    # must reject the request; never silently downgrade to unconstrained text.
    return "json_schema"


def format_parameters(mode, name, response_model):
    if mode == "text":
        return {}
    if mode == "json_object":
        return {"response_format": {"type": "json_object"}}
    return {"response_format": {"type": "json_schema", "json_schema": {
        "name": name, "strict": True, "schema": response_model.model_json_schema(),
    }}}


def format_instructions(response_model, *, include_schema=True):
    example = ""
    if response_model.__name__ in {"GeneratedPlan", "RepairPlan"}:
        # Illustrate separate objects using real source strings, without suggesting
        # a task implementation or silently repairing an ambiguous answer.
        example = (
            '\nSerialization example for two files (structure only, not an implementation): '
            + json.dumps({"files": [
                {"path": "models/first.py", "content": 'MESSAGE = "Hello"\nPATTERN = r"\\d+"\n'},
                {"path": "models/second.py", "content": 'PAYLOAD = {"name": "reader"}\n'},
            ]}, separators=(",", ":"))
            + '\nReturn all required fields and complete task-specific source. Do not copy the example files.'
        )
    return (
        "Serialize one complete JSON object. No Markdown, prose outside JSON, or extra fields. "
        "Every object member name must occur exactly once, at EVERY nesting level. "
        "Use precisely the fields in the supplied schema. Put separate files/routes in separate array objects. "
        "Never add a second path/content pair to an existing object. "
        "Return strict RFC 8259 JSON. JSON-escape source newlines, quotes, and backslashes in every file content string. "
        "Use escaped newlines, including in multiline Python strings. A Python raw string still needs JSON escaping. "
        "Encode embedded JSON (such as database/schema.json) as a string, escaping its quotes too. "
        "Never put physical control characters or unescaped source quotes inside a JSON string; no comments, trailing commas, NaN or Infinity. "
        "Complete the whole object before ending. "
        + ("Follow this schema: " + json.dumps(response_model.model_json_schema(), separators=(",", ":")) if include_schema else "Follow the supplied response schema. ")
        + example
    )
