"""Proxy mock requests to isolated project workers."""

import asyncio
import os
import sys
import time
from uuid import uuid4
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from reqlica.projects.authentication import authentication_error
from reqlica.projects.scenarios import consume_scenario
from reqlica.projects.inspection import record_project_request
from reqlica.projects.reset import DataReset
from pydantic import BaseModel, ConfigDict, Field, field_validator
from reqlica.projects.store import create_project as create_project_files
from reqlica.runtime.schema_routes import router as schema_router
from reqlica.runtime.cors import ProjectCorsMiddleware
from reqlica.runtime.worker import (
    ProjectActivatingError,
    ProjectStoppedError,
    WorkerManager,
)
from starlette.routing import compile_path

SUPPORTED_METHODS = [
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "HEAD",
    "TRACE",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_manager = WorkerManager()
    app.state.worker_manager = worker_manager
    try:
        timeout = httpx.Timeout(connect=5, read=None, write=30, pool=5)
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=timeout
        ) as proxy_client:
            app.state.proxy_client = proxy_client
            yield
    finally:
        await asyncio.to_thread(worker_manager.close)


app = FastAPI(title="Reqlica Mock Server", lifespan=lifespan)
app.add_middleware(ProjectCorsMiddleware)
app.include_router(schema_router)
from reqlica.runtime.operations import router as maintenance_router
app.include_router(maintenance_router)
_creation_lock = Lock()


def _filter_proxy_headers(
    headers: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    """Remove connection-only headers while preserving repeated headers like cookies."""
    excluded_headers = {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"proxy-connection",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
    }
    for name, value in headers:
        if name.lower() == b"connection":
            for token in value.split(b","):
                excluded_headers.add(token.strip().lower())

    forwarded_headers = []
    for name, value in headers:
        if name.lower() not in excluded_headers:
            forwarded_headers.append((name, value))
    return forwarded_headers


@app.get("/api/health", tags=["Runtime"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "reqlica-runtime"}


@app.get("/mock/demo/hello", tags=["Demo"])
def hello() -> dict[str, str]:
    """Return a sample response for checking the mock server connection."""
    return {"message": "Hello from Reqlica", "project_id": "demo"}


def _with_urls(request: Request, project: dict) -> dict:
    url = f"{str(request.base_url).rstrip('/')}/mock/{project['id']}"
    return {
        **project,
        "url": url,
        "endpoints": [
            {**endpoint, "url": url + compile_path(endpoint["path"])[1]}
            for endpoint in project["endpoints"]
        ],
    }


@app.get('/api/runtime-environment', tags=['Runtime'])
def runtime_environment():
    """Identify the Python environment inherited by project workers."""
    return {'prefix': str(Path(sys.prefix).resolve()), 'python': sys.executable,
            'python_version': list(sys.version_info[:2])}


@app.get("/api/projects", tags=["Projects"])
def list_projects(request: Request, running: bool = False) -> dict:
    """List registered projects, live worker status, and endpoint URLs without starting them."""
    projects = request.app.state.worker_manager.list_projects()
    return {
        "projects": [
            _with_urls(request, project)
            for project in projects
            if not running or project["status"] == "running"
        ]
    }


class ProjectCreation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = Field(
        min_length=1,
        description="New or empty server-side absolute directory; ~/ paths are expanded.",
    )
    name: str | None = Field(
        default=None, min_length=1, description="Defaults to the directory's basename."
    )

    @field_validator("directory")
    @classmethod
    def absolute_directory(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Provide a new or empty absolute project directory")
        try:
            directory = Path(value).expanduser()
        except RuntimeError as error:
            raise ValueError("Could not expand the project directory") from error
        if not directory.is_absolute():
            raise ValueError("Project directory must be absolute (or start with ~/)")
        return str(directory)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("Project name cannot be blank")
        return value


@app.post("/api/projects", tags=["Projects"], status_code=201)
def create_project(request: Request, body: ProjectCreation) -> dict:
    """Scaffold and register a project on the server's filesystem; leave it idle."""
    try:
        # Serialize HTTP creation so concurrent requests for the same empty
        # directory cannot interleave scaffolding or roll back each other's files.
        with _creation_lock:
            project = create_project_files(Path(body.directory), body.name)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except (ValueError, FileExistsError, NotADirectoryError) as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(500, str(error)) from error
    return _manage_project(request, project.id)


def _manage_project(
    request: Request, project_id: str, action: str | None = None
) -> dict:
    manager = request.app.state.worker_manager
    try:
        if action == "start":
            project = manager.start(project_id)
        elif action == "stop":
            project = manager.stop(project_id)
        else:
            project = manager.describe_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectActivatingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _with_urls(request, project)


@app.get("/api/projects/{project_id}/endpoints", tags=["Projects"])
def project_endpoints(request: Request, project_id: str) -> dict:
    """Show loaded routes for running workers, or configured routes for inactive projects."""
    return _manage_project(request, project_id)


@app.post("/api/projects/{project_id}/start", tags=["Projects"])
def start_project(request: Request, project_id: str) -> dict:
    """Start a worker now, or reuse it if already running."""
    return _manage_project(request, project_id, "start")


@app.post("/api/projects/{project_id}/stop", tags=["Projects"])
def stop_project(request: Request, project_id: str) -> dict:
    """Stop this worker and prevent lazy restart until explicitly started again."""
    return _manage_project(request, project_id, "stop")


class ProjectDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delete_files: bool = False
    expected_directory: str | None = None


def _delete_operation(request: Request, project_id: str, body: ProjectDeletion) -> dict:
    manager = request.app.state.worker_manager
    try:
        return manager.delete(project_id, body.delete_files, body.expected_directory)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ProjectActivatingError as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(502, str(error)) from error


@app.get("/api/projects/{project_id}/deletion", tags=["Projects"])
def project_deletion_info(
    request: Request, project_id: str, delete_files: bool = False
) -> dict:
    try:
        return request.app.state.worker_manager.deletion_info(project_id, delete_files)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.delete("/api/projects/{project_id}", tags=["Projects"])
def delete_project(request: Request, project_id: str, body: ProjectDeletion) -> dict:
    return _delete_operation(request, project_id, body)


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def _publish(
    request: Request, project_id: str, activation: Activation | None = None
) -> dict:
    try:
        manager = request.app.state.worker_manager
        if activation is None:
            result = manager.convert(project_id)
        else:
            result = manager.activate(
                project_id, activation.draft_id, activation.expected_revision
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectActivatingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _with_urls(request, result)


@app.post("/api/projects/{project_id}/convert", tags=["Projects"])
def convert_project(request: Request, project_id: str) -> dict:
    return _publish(request, project_id)


@app.post("/api/projects/{project_id}/activate", tags=["Projects"])
def activate_project(request: Request, project_id: str, activation: Activation) -> dict:
    return _publish(request, project_id, activation)


@app.post("/api/projects/{project_id}/data-reset", tags=["Projects"])
def reset_project_data(request: Request, project_id: str, body: DataReset) -> dict:
    from reqlica.runtime.reset import reset_data
    try:
        return reset_data(request.app.state.worker_manager, project_id, body)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ProjectActivatingError as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(503, str(error)) from error


@app.api_route("/mock/{project_id}", methods=SUPPORTED_METHODS)
@app.api_route("/mock/{project_id}/{path:path}", methods=SUPPORTED_METHODS)
async def proxy(request: Request, project_id: str, path: str = "") -> Response:
    request_id = uuid4().hex
    request.state.reqlica_request_id = request_id
    started = time.monotonic()
    status, outcome, response = 500, "gateway_error", None
    try:
        response = await _dispatch_proxy(request, project_id, path)
        status, outcome = response.status_code, "response"
        response.headers["X-Reqlica-Request-ID"] = request_id
        return response
    except HTTPException as error:
        status, outcome = error.status_code, "gateway_error"
        error.headers = {**(error.headers or {}), "X-Reqlica-Request-ID": request_id}
        raise
    except asyncio.CancelledError:
        status, outcome = 499, "cancelled"
        raise
    finally:
        if isinstance(request.method, str):
            await asyncio.to_thread(record_project_request, project_id, {
                "kind": "request", "request_id": request_id,
                "method": request.method, "path": ("/" + path)[:1024],
                "status_code": status, "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "response_bytes": len(response.body) if response is not None else None,
            }, getattr(request, "_body", None), response.body if response is not None else None)


async def _dispatch_proxy(request: Request, project_id: str, path: str = "") -> Response:
    worker_manager = request.app.state.worker_manager
    acquisition = asyncio.create_task(
        asyncio.to_thread(worker_manager.acquire_request, project_id)
    )
    try:
        # Startup may wait for imports; keep other requests responsive while it runs.
        worker = await asyncio.shield(acquisition)
    except asyncio.CancelledError:

        def release_abandoned(task):
            if not task.cancelled() and task.exception() is None:
                worker_manager.release_request(project_id)

        acquisition.add_done_callback(release_abandoned)
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoppedError, ProjectActivatingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        return await _proxy_to_worker(request, worker)
    finally:
        worker_manager.release_request(project_id)


async def _proxy_to_worker(request: Request, worker) -> Response:
    try:
        failure = await asyncio.to_thread(authentication_error, worker.project.directory, request.headers)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Mock authentication settings unavailable") from exc
    if failure:
        code, headers = failure
        return JSONResponse({"detail": code}, status_code=401,
                            headers={**headers, "Cache-Control": "no-store"})
    try:
        # ASGI path has the same decoding as the worker router; query is separate.
        path = request.scope["path"].split("/", 3)
        route_path = "/" + path[3] if len(path) == 4 else "/"
        scenario = await asyncio.to_thread(consume_scenario, worker.project.directory,
                                          request.method, route_path, worker.manifest, request.headers)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Scenario settings unavailable") from exc
    if scenario:
        if "rate_limit_error" in scenario:
            headers = {"Cache-Control": "no-store"}
            if "retry_after" in scenario:
                headers["Retry-After"] = str(scenario["retry_after"])
            response = JSONResponse({"detail": scenario["rate_limit_error"]},
                                    status_code=scenario["rate_limit_status"], headers=headers)
            if request.method == "HEAD":
                response.body = b""
            return response
        await asyncio.sleep(scenario["delay_ms"] / 1000)
        if scenario["response"] != "passthrough":
            response = JSONResponse(
                [] if scenario["response"] == "empty" else scenario["body"],
                status_code=200 if scenario["response"] == "empty" else scenario["status_code"],
                headers={"Cache-Control": "no-store", "X-Reqlica-Scenario": scenario["response"]},
            )
            if request.method == "HEAD":
                response.body = b""
            return response
    # Strip the gateway prefix without decoding escaped slashes or other path bytes.
    path_parts = request.scope["raw_path"].split(b"/", maxsplit=3)
    endpoint_path = b"/"
    if len(path_parts) == 4:
        endpoint_path += path_parts[3]
    query_string = request.scope["query_string"]
    if query_string:
        endpoint_path += b"?" + query_string

    worker_url = httpx.URL(f"http://127.0.0.1:{worker.port}")
    worker_url = worker_url.copy_with(raw_path=endpoint_path)
    proxy_client = request.app.state.proxy_client
    try:
        headers = []
        for name, value in _filter_proxy_headers(request.scope["headers"]):
            if name.lower() in {b"x-forwarded-proto", b"x-forwarded-for", b"x-reqlica-request-id"}:
                continue
            headers.append((name, value))
        headers.append((b"x-forwarded-proto", request.scope["scheme"].encode("ascii")))
        headers.append((b"x-reqlica-request-id", request.state.reqlica_request_id.encode("ascii")))

        # Do not merge the shared client's cookie jar into a caller's request.
        worker_request = httpx.Request(
            request.method,
            worker_url,
            headers=headers,
            content=await request.body(),
        )
        worker_response = await proxy_client.send(worker_request, stream=True)
        try:
            # Keep raw bytes so compressed bodies still match their response headers.
            chunks = []
            async for chunk in worker_response.aiter_raw():
                chunks.append(chunk)
            response = Response(
                content=b"".join(chunks), status_code=worker_response.status_code
            )
            response.raw_headers = _filter_proxy_headers(worker_response.headers.raw)
            return response
        finally:
            await worker_response.aclose()
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(
            status_code=502, detail=f"Project worker request failed: {exc}"
        ) from exc


def main() -> None:
    """Start the mock server using settings from the repository's .env file."""
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    host = os.environ.get("MOCK_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCK_PORT", "4000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
