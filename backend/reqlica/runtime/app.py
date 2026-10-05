"""Proxy mock requests to isolated project workers."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response

from reqlica.runtime.worker import WorkerManager

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


@app.get("/mock/demo/hello", tags=["Demo"])
def hello() -> dict[str, str]:
    """Return a sample response for checking the mock server connection."""
    return {"message": "Hello from Reqlica", "project_id": "demo"}


@app.api_route("/mock/{project_id}", methods=SUPPORTED_METHODS)
@app.api_route("/mock/{project_id}/{path:path}", methods=SUPPORTED_METHODS)
async def proxy(request: Request, project_id: str, path: str = "") -> Response:
    try:
        worker_manager = request.app.state.worker_manager
        # Startup may wait for imports; keep other requests responsive while it runs.
        worker = await asyncio.to_thread(worker_manager.get_or_start, project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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
            if name.lower() in {b"x-forwarded-proto", b"x-forwarded-for"}:
                continue
            headers.append((name, value))
        headers.append((b"x-forwarded-proto", request.scope["scheme"].encode("ascii")))

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
