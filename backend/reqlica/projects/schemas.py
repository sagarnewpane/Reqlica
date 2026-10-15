"""Revision-linked generated OpenAPI artifacts, outside editable/published code."""

import copy
import hashlib
import json
import re
from pathlib import Path

from fastapi.openapi.models import OpenAPI
from reqlica.projects import drafts
from reqlica.projects.store import get_project
from reqlica.projects.versions import (
    _read_code_file,
    atomic_write_json,
    code_directory,
    content_digest,
    project_lock,
)
from reqlica.validation.openapi import HTTP_METHODS, OPENAPI_LIMIT
from starlette.routing import compile_path


def _artifact(project, kind: str, identity: str, revision: str) -> Path:
    if (
        kind not in {"drafts", "versions"}
        or not re.fullmatch(r"[0-9a-f]{32}", identity)
        or not re.fullmatch(r"[0-9a-f]{64}", revision)
    ):
        raise ValueError("Invalid schema identity or revision")
    return drafts._contained(
        project.directory, f".reqlica/openapi/{kind}/{identity}/{revision}.json"
    )


def _read_json(project, path: Path, limit: int) -> tuple[dict, bytes]:
    drafts._contained(project.directory, path.relative_to(project.directory).as_posix())
    if path.stat().st_size > limit:
        raise ValueError("Schema artifact exceeds its size limit")
    raw = _read_code_file(project.directory, path.relative_to(project.directory))
    if len(raw) > limit:
        raise ValueError("Schema artifact exceeds its size limit")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("Invalid schema artifact")
    return data, raw


def _store(project, kind: str, identity: str, revision: str, document: dict) -> dict:
    document = copy.deepcopy(document)
    document["x-reqlica-project-id"] = project.id
    document["x-reqlica-revision"] = revision
    document.pop("x-reqlica-draft-id", None)
    document.pop("x-reqlica-version-id", None)
    document["x-reqlica-draft-id" if kind == "drafts" else "x-reqlica-version-id"] = (
        identity
    )
    OpenAPI.model_validate(document)
    path = _artifact(project, kind, identity, revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, document)
    raw = _read_code_file(project.directory, path.relative_to(project.directory))
    if len(raw) > OPENAPI_LIMIT:
        raise ValueError("Generated OpenAPI exceeds the 1 MiB schema limit")
    atomic_write_json(
        path.with_suffix(".metadata.json"),
        {"revision": revision, "sha256": hashlib.sha256(raw).hexdigest()},
    )
    warnings = document.get("x-reqlica-contract-warnings", [])
    return {
        "status": "incomplete" if warnings else "complete",
        "revision": revision,
        "artifact": path.relative_to(project.directory).as_posix(),
        "warning_count": len(warnings),
        "warnings": warnings[:20],
    }


def store_draft_schema(project, draft_id: str, revision: str, document: dict) -> dict:
    """Called under project_lock only after snapshot/revision integrity checks pass."""
    return _store(project, "drafts", draft_id, revision, document)


def _load(project, kind: str, identity: str, revision: str) -> dict:
    path = _artifact(project, kind, identity, revision)
    try:
        document, raw = _read_json(project, path, OPENAPI_LIMIT)
        metadata, _ = _read_json(project, path.with_suffix(".metadata.json"), 4096)
    except FileNotFoundError as error:
        raise ValueError(
            "No generated schema for this revision. Validate the draft; published schemas become available after validated activation."
        ) from error
    except TypeError as error:
        raise ValueError("Invalid schema artifact; validate the draft again") from error
    if (
        metadata.get("revision") != revision
        or metadata.get("sha256") != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError(
            "Schema artifact changed or is incomplete; validate the draft again"
        )
    identity_key = "x-reqlica-draft-id" if kind == "drafts" else "x-reqlica-version-id"
    if (
        document.get("x-reqlica-project-id") != project.id
        or document.get("x-reqlica-revision") != revision
        or document.get(identity_key) != identity
    ):
        raise ValueError("Schema artifact does not match this project/revision")
    OpenAPI.model_validate(document)
    return document


def get_api_schema(
    project_id: str,
    draft_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
) -> dict:
    """Read saved contracts only; never import code, rerun checks, or clear approval."""
    if (method is None) != (path is None):
        raise ValueError(
            "Supply method and path together, or omit both for the full API"
        )
    if method is not None and method.lower() not in HTTP_METHODS:
        raise ValueError("Unsupported HTTP method")
    if path is not None and (
        not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path
    ):
        raise ValueError("Use a registered route pattern, not a URL or query string")
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        if draft_id is not None:
            state = drafts.inspect_draft(project_id, draft_id)
            revision = state["revision"]
            validation = state.get("validation")
            if (
                not validation
                or validation.get("status") != "passed"
                or validation.get("revision") != revision
                or not validation.get("schema")
            ):
                raise ValueError(
                    "Draft schema is missing or stale. Run validate_draft for the current revision first."
                )
            document = _load(project, "drafts", draft_id, revision)
        else:
            if project.active_version is None:
                raise ValueError(
                    "Legacy projects have no published schema. Convert, validate a draft, and activate it first."
                )
            revision = content_digest(code_directory(project))
            document = _load(project, "versions", project.active_version, revision)
        if path is not None:
            pattern = compile_path(path)[1]
            operation = document.get("paths", {}).get(pattern, {}).get(method.lower())
            if operation is None:
                raise FileNotFoundError(
                    f"No endpoint schema for {method.upper()} {path}"
                )
            document["paths"] = {pattern: {method.lower(): operation}}
            document["x-reqlica-contract-warnings"] = [
                warning
                for warning in document.get("x-reqlica-contract-warnings", [])
                if warning["method"] == method.upper() and warning["path"] == pattern
            ]
        warnings = document.get("x-reqlica-contract-warnings", [])
        return {
            "project_id": project_id,
            "draft_id": draft_id,
            "version_id": None if draft_id is not None else project.active_version,
            "revision": revision,
            "contract_status": "incomplete" if warnings else "complete",
            "warnings": warnings,
            "schema": document,
        }


def promote_draft_schema(
    project, draft_id: str, version_id: str, revision: str
) -> None:
    """Prepare the approved version's artifact before the active pointer commits."""
    state = drafts.inspect_draft(project.id, draft_id)
    if state["revision"] != revision:
        raise ValueError("Draft changed while publishing its schema")
    if not (state.get("validation") or {}).get("schema"):
        return  # Compatibility for revisions approved before schema generation existed.
    document = get_api_schema(project.id, draft_id)["schema"]
    _store(project, "versions", version_id, revision, document)


def serving_document(result: dict, base_url: str, *, preview: bool = False) -> dict:
    """Force Swagger requests onto the intended API, never a cached server URL."""
    document = copy.deepcopy(result["schema"])
    document["servers"] = [{"url": base_url}]
    for item in document.get("paths", {}).values():
        item.pop("servers", None)
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operation.pop("servers", None)
            if preview:
                parameters = [
                    parameter
                    for parameter in operation.get("parameters", [])
                    if not (
                        parameter.get("in") == "header"
                        and parameter.get("name", "").lower()
                        == "x-reqlica-expected-revision"
                    )
                ]
                parameters.append(
                    {
                        "name": "X-Reqlica-Expected-Revision",
                        "in": "header",
                        "required": True,
                        "description": "Reqlica's revision pin. Revalidate/reload documentation after edits.",
                        "schema": {
                            "type": "string",
                            "enum": [result["revision"]],
                            "default": result["revision"],
                        },
                    }
                )
                operation["parameters"] = parameters
    note = (
        "Draft preview: every request uses disposable data; writes are discarded. Only JSON bodies and query/path inputs are forwarded; custom request headers/auth/cookie sessions are not supported."
        if preview
        else "Published API: requests use persistent live project data."
    )
    document.setdefault("info", {})["description"] = (
        note + "\n\n" + (document.get("info", {}).get("description") or "")
    )
    return document
