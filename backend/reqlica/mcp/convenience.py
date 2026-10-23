"""Optional URL-discovery helpers, not prerequisites for the core MCP workflow."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from reqlica.cli import _console_base_url, _mock_base_url
from reqlica.mcp.parameters import DraftID, ProjectID
from reqlica.projects import urls
from starlette.concurrency import run_in_threadpool

RouteFilter = Annotated[
    str | None,
    Field(
        description=(
            "Optional exact registered route pattern, e.g. /hello or /books/{book_id}. "
            "Omit to list all endpoint URLs. Not a full URL or concrete placeholder value."
        ),
        examples=["/hello", "/books/{book_id}"],
    ),
]


async def get_project_urls(project_id: ProjectID, path: RouteFilter = None) -> dict:
    """Convenience only: get published endpoint URLs for the user to call directly.

    Returns kind='published', base_url, active_version and endpoints with method,
    path, handler, url, requires_path_parameters. Optional path filters registered
    patterns. Use the listed method and replace placeholders; published requests
    use persistent live data. No draft-only routes or revision input.

    Reads definitions only, without worker startup, handler execution or gateway
    health guarantees. Optional; not required for core creation/edit/check tools.
    """
    return await run_in_threadpool(
        urls.get_project_urls, project_id, _mock_base_url(), path
    )


async def get_draft_urls(
    project_id: ProjectID, draft_id: DraftID, path: RouteFilter = None
) -> dict:
    """Convenience only: get draft preview URLs for browser, curl, or Postman testing.

    Returns kind='draft_preview', draft_id, current revision, base_url and endpoint
    method/path/handler/url/requires_path_parameters. Requires an existing draft,
    no revision input. Optional path filters registered patterns. Replace placeholders;
    use GET in browsers, curl/Postman for other methods. Console must be running.

    Generating URLs executes no handler; following one executes the CURRENT draft,
    unpinned, with a fresh disposable DB per request. SDK writes never reach live
    data or persist between calls. JSON bodies and bounded JSON/text diagnostics
    only; no custom headers/cookie sessions, streaming or binary proxy. Responses
    expose X-Reqlica-Draft-Revision. Python is not sandboxed.
    No validation, approval or publication. Optional; use call_draft_endpoint for
    revision-explicit LLM checks.
    """
    return await run_in_threadpool(
        urls.get_draft_urls, project_id, draft_id, _console_base_url(), path
    )


def register_convenience_tools(server: FastMCP) -> None:
    """Keep optional helpers visibly separate from required authoring/check tools."""
    for operation in (get_project_urls, get_draft_urls):
        server.add_tool(
            operation,
            annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            meta={"reqlica/category": "convenience"},
        )
