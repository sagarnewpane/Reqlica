"""Configure the console's FastAPI application.

Register management API routes and the AI-facing MCP endpoint.
Mock API traffic and generated endpoint execution belong to reqlica.runtime.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from reqlica.console import dashboard_api
from reqlica.console.dashboard import Dashboard
from reqlica.console.routes import (
    cors,
    data,
    drafts,
    environment,
    generation,
    logs,
    maintenance,
    packages,
    preview,
    projects,
    provider_keys,
    reset,
    runtime,
    scenarios,
    schemas,
    validation,
    workspace,
)
from reqlica.mcp.server import create_server


def health() -> dict[str, str]:
    """Report that the console server is responding, not project worker health."""
    return {"status": "ok", "service": "reqlica-console"}


def create_app(*, frontend_directory: Path | None = None) -> FastAPI:
    """Serve the dashboard, management API, and MCP on one host and port."""
    mcp = create_server()
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Console", lifespan=lifespan)

    @app.exception_handler(HTTPException)
    async def dashboard_error(request, error):
        if request.url.path.startswith("/api/ui/"):
            return JSONResponse(
                {"error": error.detail},
                status_code=error.status_code,
                headers={**(error.headers or {}), "Cache-Control": "no-store"},
            )
        return await http_exception_handler(request, error)

    app.include_router(dashboard_api.router)
    app.include_router(drafts.router)
    app.include_router(validation.router)
    app.include_router(preview.router)
    app.include_router(schemas.router)
    app.include_router(runtime.router)
    app.include_router(generation.router)
    app.include_router(generation.provider_router)
    app.include_router(provider_keys.router)
    app.include_router(provider_keys.app_router)
    app.include_router(data.router)
    app.include_router(environment.router)
    app.include_router(scenarios.router)
    app.include_router(scenarios.scenario_router)
    app.include_router(logs.router)
    app.include_router(reset.router)
    app.include_router(projects.router)
    app.include_router(cors.router)
    app.include_router(maintenance.router)
    app.include_router(workspace.router)
    app.include_router(packages.router)
    app.add_api_route("/api/health", health, methods=["GET"], tags=["Console"])
    # API routes take precedence over dashboard files and the MCP transport.
    app.mount("/", Dashboard(mcp_app, frontend_directory))
    return app


app = create_app()


def main() -> None:
    """Start the console using settings from the repository's .env file."""
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    host = os.environ.get("CONSOLE_HOST", "127.0.0.1")
    port = int(os.environ.get("CONSOLE_PORT", "3000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
