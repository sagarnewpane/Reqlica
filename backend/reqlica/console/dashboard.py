"""Serve the bundled dashboard without a Node.js runtime."""

import re
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)


class Dashboard:
    def __init__(self, mcp_app, directory: Path | None = None):
        self.mcp_app = mcp_app
        self.directory = directory or Path(__file__).resolve().parents[1] / "static"

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "/")
        # MCP owns its transport path; unknown API paths must never return UI HTML.
        if (
            scope["type"] != "http"
            or path == "/mcp"
            or path.startswith(("/mcp/", "/api/"))
        ):
            await self.mcp_app(scope, receive, send)
            return
        request = Request(scope)
        if request.method not in {"GET", "HEAD"}:
            await JSONResponse({"detail": "Method not allowed"}, status_code=405)(
                scope, receive, send
            )
            return
        legacy = re.fullmatch(r"/projects/([a-f0-9]{32})/?", path)
        if legacy:
            query = request.query_params.multi_items()
            query = [(k, v) for k, v in query if k != "projectId"] + [
                ("projectId", legacy[1])
            ]
            await RedirectResponse("/project?" + urlencode(query), status_code=307)(
                scope, receive, send
            )
            return
        root = self.directory.resolve()
        relative = path.lstrip("/") or "index.html"
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            await JSONResponse({"detail": "Not found"}, status_code=404)(
                scope, receive, send
            )
            return
        if not target.is_file() and not Path(relative).suffix:
            target = target.with_suffix(".html").resolve()
        if not target.is_relative_to(root):
            await JSONResponse({"detail": "Not found"}, status_code=404)(
                scope, receive, send
            )
            return
        if target.is_file():
            cache = (
                "public, max-age=31536000, immutable"
                if path.startswith("/_next/static/")
                else "no-cache"
            )
            await FileResponse(target, headers={"Cache-Control": cache})(
                scope, receive, send
            )
            return
        if path == "/" and not (root / "index.html").is_file():
            await HTMLResponse(
                "<h1>Reqlica dashboard is not built</h1><p>From the source checkout, run <code>python3 scripts/build_frontend.py</code>, then reload.</p>",
                status_code=503,
            )(scope, receive, send)
            return
        not_found = root / "404.html"
        response = (
            FileResponse(not_found, status_code=404)
            if not_found.is_file() and not path.startswith("/_next/")
            else JSONResponse({"detail": "Not found"}, status_code=404)
        )
        await response(scope, receive, send)
