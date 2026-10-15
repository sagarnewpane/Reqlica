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

    Use when the user asks for live API URLs, not when implementing/checking a
    draft. Returns kind='published', base_url, active_version, and endpoints with
    method, path, handler, url, requires_path_parameters. Optional path filters
    by registered route pattern. Use the listed HTTP method and replace path
    placeholders. Published calls use persistent live data, unlike draft previews.

    Reads definitions only; does not start a worker, execute handlers, or guarantee
    the gateway is running. Does not reveal draft-only endpoints. No revision is
    needed. This helper is NOT a prerequisite for any core creation/edit/check tool.
    """
    return await run_in_threadpool(
        urls.get_project_urls, project_id, _mock_base_url(), path
    )


async def get_draft_urls(
    project_id: ProjectID, draft_id: DraftID, path: RouteFilter = None
) -> dict:
    """Convenience only: get draft preview URLs for browser, curl, or Postman testing.

    Use when the user wants a URL to try unpublished routes without copying a
    revision hash. Requires an existing draft from create_draft; no revision input
    is needed. Returns kind='draft_preview', draft_id, current revision, base_url,
    and endpoint method/path/handler/url/requires_path_parameters. Optional path
    filters by exact registered route pattern, e.g. '/hello'. Replace placeholders
    in URL patterns before requesting; use GET for browser links, curl/Postman for
    POST/etc. The console must be running. No custom request headers/cookie sessions.

    URL generation itself executes no handler. Following a URL executes the CURRENT
    draft at request time, with a fresh disposable database copy on EACH request;
    writes do not persist between calls or reach live data through the SDK. JSON
    request bodies and bounded JSON/text diagnostic responses only. These URLs are
    not version-pinned and not a streaming/binary proxy; responses expose the actual
    revision in X-Reqlica-Draft-Revision. They do not validate, approve, or publish.
    Keep core call_draft_endpoint for revision-explicit LLM checks. This helper is
    optional and is NOT a prerequisite for the core workflow. Python is not sandboxed.
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
