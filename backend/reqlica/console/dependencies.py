"""Small HTTP adapters for the local, trusted-code management API."""

import re
from collections.abc import Callable

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from reqlica.cli import _mock_base_url
from reqlica.generation.progress import track
from reqlica.projects import drafts
from reqlica.projects.store import get_project
from reqlica.projects.versions import _read_code_file, content_digest, project_lock


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def draft_operation(operation: Callable, *args, **kwargs):
    """Keep domain failures distinct from missing resources and server IO errors."""
    try:
        return operation(*args, **kwargs)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(500, str(error)) from error


def list_drafts(project_id: str) -> dict:
    return drafts.list_drafts(project_id)


def activity_operation(project_id: str, activity_id: str | None, operation: Callable, *args):
    def execute():
        with track(project_id, activity_id):
            return operation(*args)

    return draft_operation(execute)


def read_draft_file(project_id: str, draft_id: str, path: str) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        state = drafts.inspect_draft(project_id, draft_id)
        directory = drafts.draft_directory(project, draft_id)
        target = drafts._contained(directory, path)
        if path not in state["files"]:
            raise FileNotFoundError(f"Unknown draft file: {path}")
        content = _read_code_file(directory, target.relative_to(directory)).decode(
            "utf-8"
        )
        if content_digest(directory) != state["revision"]:
            raise ValueError("Draft changed while reading; inspect the current draft")
        return {"path": path, "content": content, "revision": state["revision"]}


def gateway_operation(project_id: str, command: str, body: dict | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise HTTPException(404, "Unknown project")
    try:
        response = httpx.request(
            "POST",
            f"{_mock_base_url()}/api/projects/{project_id}/{command}",
            json=body,
            timeout=60.0,
            trust_env=False,
        )
    except (httpx.HTTPError, OSError, ValueError, httpx.InvalidURL) as error:
        raise HTTPException(
            502, f"Could not reach the mock gateway: {error}"
        ) from error
    if not response.is_success:
        try:
            payload = response.json()
            detail = (
                payload.get("detail", payload) if isinstance(payload, dict) else payload
            )
        except ValueError:
            detail = response.text or "No error detail returned"
        raise HTTPException(response.status_code, detail)
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Expected a JSON object")
        return payload
    except (ValueError, TypeError) as error:
        raise HTTPException(502, f"Malformed mock gateway reply: {error}") from error
