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
    reset,
    runtime,
    scenarios,
    schemas,
    validation,
)
from reqlica.mcp.server import create_server


def health() -> dict[str, str]:
    """Report that the console server is responding, not project worker health."""
    return {"status": "ok", "service": "reqlica-console"}


def create_app() -> FastAPI:
    """Expose MCP automatically with the console, sharing its host and port."""
    mcp = create_server()
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Console", lifespan=lifespan)
    app.include_router(drafts.router)
    app.include_router(validation.router)
    app.include_router(preview.router)
    app.include_router(schemas.router)
    app.include_router(runtime.router)
    app.include_router(generation.router)
    app.include_router(generation.provider_router)
    app.include_router(data.router)
    app.include_router(environment.router)
    app.include_router(scenarios.router)
    app.include_router(scenarios.scenario_router)
    app.include_router(logs.router)
    app.include_router(reset.router)
    app.include_router(projects.router)
    app.include_router(cors.router)
    app.include_router(maintenance.router)
    app.include_router(packages.router)
    app.add_api_route("/api/health", health, methods=["GET"], tags=["Console"])
    # Keep this last: the transport handles /mcp and returns 404 for other paths.
    app.mount("/", mcp_app)
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
