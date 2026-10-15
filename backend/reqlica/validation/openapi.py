"""Generate contracts from the actual FastAPI app inside the disposable runner."""

import json

from fastapi import FastAPI
from fastapi.openapi.models import OpenAPI
from fastapi.routing import APIRoute
from fastapi.security.base import SecurityBase

OPENAPI_LIMIT = 1_048_576
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _incomplete(schema, components: dict, seen: frozenset = frozenset()) -> bool:
    if not isinstance(schema, dict) or not schema:
        return True
    reference = schema.get("$ref")
    if reference:
        if reference in seen:
            return False
        if not reference.startswith("#/components/schemas/"):
            return True
        name = (
            reference.removeprefix("#/components/schemas/")
            .replace("~1", "/")
            .replace("~0", "~")
        )
        return _incomplete(components.get(name), components, seen | {reference})
    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword in schema:
            return any(_incomplete(item, components, seen) for item in schema[keyword])
    if schema.get("type") == "object":
        properties = schema.get("properties")
        return not properties or any(
            _incomplete(item, components, seen) for item in properties.values()
        )
    if schema.get("type") == "array":
        return _incomplete(schema.get("items"), components, seen)
    return not (schema.get("type") or "enum" in schema or "const" in schema)


def _uses_raw_request(dependant) -> bool:
    if isinstance(dependant.call, SecurityBase):
        return False  # FastAPI already describes supported security dependencies.
    return bool(dependant.request_param_name) or any(
        _uses_raw_request(child) for child in dependant.dependencies
    )


def generate_openapi(app: FastAPI) -> dict:
    """Document typed contracts; flag unknown/free-form inputs instead of guessing."""
    document = app.openapi()
    OpenAPI.model_validate(document)
    components = document.get("components", {}).get("schemas", {})
    warnings = []
    seen_operations = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            key = (route.path_format, method.lower())
            if key in seen_operations:
                raise ValueError(
                    f"Routes collide in OpenAPI: {method} {route.path_format}"
                )
            seen_operations.add(key)
            operation = document["paths"][route.path_format][method.lower()]

            def warn(
                location: str, message: str, method=method, path=route.path_format
            ) -> None:
                warnings.append(
                    {
                        "method": method,
                        "path": path,
                        "location": location,
                        "message": message,
                    }
                )

            if _uses_raw_request(route.dependant):
                warn(
                    "request",
                    "Raw Request access may hide expected inputs. Declare Body, Query, Path, Header or typed dependencies; no payload fields were inferred.",
                )
            for parameter in operation.get("parameters", []):
                if _incomplete(parameter.get("schema"), components):
                    warn(
                        f"{parameter['in']}.{parameter['name']}",
                        "Parameter type is incomplete; add an explicit type annotation.",
                    )
            for media in operation.get("requestBody", {}).get("content", {}).values():
                if _incomplete(media.get("schema"), components):
                    warn(
                        "request.body",
                        "Body is unconstrained or contains free-form fields. Use a Pydantic model if fixed fields are expected; their names cannot be inferred from this schema.",
                    )
            if method != "HEAD":
                for code, response in operation.get("responses", {}).items():
                    if not code.startswith("2") or code in {"204", "205"}:
                        continue
                    content = response.get("content", {})
                    if not content or any(
                        _incomplete(media.get("schema"), components)
                        for media in content.values()
                    ):
                        warn(
                            f"response.{code}",
                            "Response shape is incomplete. Add a typed return model if callers need named fields; response fields were not inferred from Python statements.",
                        )
    document["x-reqlica-contract-warnings"] = warnings
    if len(json.dumps(document, allow_nan=False).encode("utf-8")) > OPENAPI_LIMIT:
        raise ValueError("Generated OpenAPI exceeds the 1 MiB schema limit")
    return document
