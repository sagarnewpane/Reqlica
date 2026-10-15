"""Read-only URL helpers; they do not create drafts, execute code, or publish."""

from urllib.parse import quote

from reqlica.projects.context import get_project_context
from starlette.routing import compile_path


def _endpoint_urls(context: dict, base_url: str, path: str | None) -> list[dict]:
    endpoints = [
        {
            **endpoint,
            "url": base_url + quote(endpoint["path"], safe="/{}:"),
            "requires_path_parameters": bool(compile_path(endpoint["path"])[2]),
        }
        for endpoint in context["endpoints"]
        if path is None or endpoint["path"] == path
    ]
    if path is not None and not endpoints:
        raise FileNotFoundError(f"No endpoint with route pattern: {path}")
    return endpoints


def get_project_urls(
    project_id: str, gateway_url: str, path: str | None = None
) -> dict:
    """Return published gateway URL patterns without starting the project worker."""
    context = get_project_context(
        project_id, include_source=False, include_database=False, include_drafts=False
    )
    if context["manifest_error"]:
        raise ValueError(context["manifest_error"])
    base_url = f"{gateway_url.rstrip('/')}/mock/{project_id}"
    return {
        "project_id": project_id,
        "active_version": context["project"]["active_version"],
        "kind": "published",
        "base_url": base_url,
        "docs_url": f"{gateway_url.rstrip('/')}/api/projects/{project_id}/docs",
        "openapi_url": f"{gateway_url.rstrip('/')}/api/projects/{project_id}/openapi.json",
        "endpoints": _endpoint_urls(context, base_url, path),
        "note": "Replace path placeholders before requesting. Gateway must be running. Docs/schema require a schema generated during draft validation and activation.",
    }


def get_draft_urls(
    project_id: str, draft_id: str, console_url: str, path: str | None = None
) -> dict:
    """Return convenience preview URL patterns for the current unpublished draft."""
    context = get_project_context(
        project_id,
        draft_id,
        include_source=False,
        include_database=False,
        include_drafts=False,
    )
    if context["manifest_error"]:
        raise ValueError(context["manifest_error"])
    base_url = (
        f"{console_url.rstrip('/')}/api/projects/{project_id}/drafts/{draft_id}/preview"
    )
    management_url = (
        f"{console_url.rstrip('/')}/api/projects/{project_id}/drafts/{draft_id}"
    )
    return {
        "project_id": project_id,
        "draft_id": draft_id,
        "revision": context["revision"],
        "kind": "draft_preview",
        "base_url": base_url,
        "docs_url": management_url + "/docs",
        "openapi_url": management_url + "/openapi.json",
        "endpoints": _endpoint_urls(context, base_url, path),
        "note": (
            "Uses the current draft at request time, not a pinned revision. "
            "Use the listed HTTP method; replace path placeholders. "
            "Each request uses disposable data; writes are discarded. "
            "JSON request bodies only; no custom request headers/cookie sessions. "
            "Responses are bounded diagnostic previews, not a streaming/binary proxy. "
            "Does not validate, approve, or publish. Console must be running."
            " Run validate_draft first to generate current schema/Swagger docs."
        ),
    }
