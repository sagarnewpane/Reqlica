"""Compact the model-facing representation without summarizing conversation facts."""

from copy import deepcopy
import json

from reqlica.generation.json_output import parse_json

_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SCHEMA_VALUES = {"items", "additionalProperties", "unevaluatedProperties", "propertyNames", "contains", "not", "if", "then", "else"}
_SCHEMA_ARRAYS = {"anyOf", "allOf", "oneOf", "prefixItems"}


def _schema_without_titles(schema):
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = {}
    for key, value in schema.items():
        if key == "title":
            continue  # A schema annotation; never a key inside a properties/default map.
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            result[key] = {name: _schema_without_titles(child) for name, child in value.items()}
        elif key in _SCHEMA_VALUES and isinstance(value, dict):
            result[key] = _schema_without_titles(value)
        elif (key in _SCHEMA_ARRAYS or key == "items") and isinstance(value, list):
            result[key] = [_schema_without_titles(child) for child in value]
        else:
            # Defaults, examples and const/enum values are data, not schemas.
            result[key] = deepcopy(value)
    return result


def model_tool_schema(schema):
    """The agent binds project/draft IDs; keep all editable/check revision guards."""
    result = _schema_without_titles(schema)
    properties = result.get("properties", {})
    for name in ("project_id", "draft_id"):
        properties.pop(name, None)
    if isinstance(result.get("required"), list):
        result["required"] = [name for name in result["required"] if name not in {"project_id", "draft_id"}]
    revision = properties.get("expected_revision")
    if isinstance(revision, dict) and "description" in revision:
        revision["description"] = "Exact current draft revision from assigned scope or the latest successful draft tool result."
    return result


def model_messages(messages):
    """Retain every message; remove only JSON formatting whitespace/Unicode escapes.

    The checkpoint, raw user/assistant text, source strings and tool-call IDs stay
    intact. Non-JSON, duplicate-key and non-finite payloads are passed unchanged.
    """
    result = []
    for message in messages:
        projected = dict(message)
        text = message.get("content")
        if message.get("role") == "tool" and isinstance(text, str):
            try:
                compact = json.dumps(parse_json(text), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(compact.encode()) < len(text.encode()):
                    projected["content"] = compact
            except (ValueError, TypeError, RecursionError):
                pass
        result.append(projected)
    return result
