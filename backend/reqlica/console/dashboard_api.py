"""Same-origin dashboard adapters over the console API and mock gateway.

Console requests stay in process. Only runtime-owned operations cross HTTP.
No request is retried: callers must inspect saved state after an uncertain write.
"""

import asyncio
import json
import math
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse, Response

from reqlica.cli import _mock_base_url

router = APIRouter(prefix="/api/ui", include_in_schema=False)
ID = r"[a-f0-9]{32}"
REVISION = re.compile(r"[a-f0-9]{64}\Z")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
RUNTIME = {
    "endpoints",
    "deletion",
    "deletion-files",
    "registration",
    "start",
    "stop",
    "activate",
    "openapi.json",
    "docs",
}
READ = {
    "packages/jobs",
    "library",
    "versions",
    "maintenance",
    "mock-auth",
    "cors",
    "scenarios",
    "data-baseline",
    "database-migrations",
    "schema-export",
    "endpoints",
    "deletion",
    "deletion-files",
    "drafts",
    "environment",
    "packages",
    "agent-runs",
    "logs",
    "logs/settings",
    "openapi.json",
    "docs",
}
WRITE = {
    "versions/restore",
    "relocate",
    "convert",
    "data-reset",
    "scenarios/reset",
    "specifications/preview",
    "drafts",
    "start",
    "stop",
    "activate",
    "packages/preview",
    "ai/models",
    "ai/key/status",
    "ai/key/reveal",
}
PATTERNS = {
    "GET": [
        rf"drafts/{ID}",
        r"logs/[1-9]\d*",
        rf"packages/jobs/{ID}",
        rf"agent-runs/{ID}(?:/context)?",
        rf"activity/{ID}",
        rf"drafts/{ID}/(?:openapi\.json|docs)",
    ],
    "PUT": [
        rf"library/{ID}",
        rf"drafts/{ID}/endpoints",
        r"environment/[A-Za-z_][A-Za-z0-9_]{0,63}",
        rf"agent-runs/{ID}",
    ],
    "PATCH": [rf"drafts/{ID}"],
    "DELETE": [
        rf"library/{ID}",
        rf"drafts/{ID}/endpoints",
        r"environment/[A-Za-z_][A-Za-z0-9_]{0,63}",
        rf"drafts/{ID}",
    ],
    "POST": [
        r"database-migrations/(?:preview|apply|rollback|recover)",
        r"environment/[A-Za-z_][A-Za-z0-9_]{0,63}/generate",
        rf"drafts/{ID}/(?:validate|test|call|approve|recover)",
        rf"drafts/{ID}/database/(?:preview|apply)",
        rf"packages/jobs/{ID}/install",
        rf"agent-runs/{ID}/(?:step|replies|recover|reasoning-default|reasoning|limits)",
    ],
}


def allowed(method, route):
    fixed = {
        "GET": READ,
        "POST": WRITE,
        "PUT": {
            "mock-auth",
            "cors",
            "scenarios",
            "data-baseline",
            "ai/key",
            "logs/settings",
        },
        "DELETE": {"ai/key", "registration"},
    }
    return route in fixed.get(method, set()) or any(
        re.fullmatch(p, route) for p in PATTERNS.get(method, [])
    )


def fail(status, message):
    raise HTTPException(status, message)


def same_origin(request):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        fail(403, "Cross-origin requests are not allowed.")
    if origin:
        try:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.netloc != request.headers.get("host")
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                fail(403, "Cross-origin requests are not allowed.")
        except ValueError:
            fail(403, "Invalid request origin.")


async def read_body(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            fail(413, "Request exceeds size limit.")
        data.extend(chunk)
    return bytes(data)


async def json_body(request, limit=1024 * 1024):
    same_origin(request)
    if (
        request.headers.get("content-type", "").split(";")[0].strip()
        != "application/json"
    ):
        fail(415, "Expected a JSON request.")
    raw = await read_body(request, limit)
    try:
        body = (
            json.loads(
                raw, parse_constant=lambda _: fail(400, "JSON numbers must be finite.")
            )
            if raw
            else {}
        )
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (ValueError, RecursionError):
        fail(400, "Expected a JSON object.")


def revision(value):
    return isinstance(value, str) and bool(REVISION.fullmatch(value))


def request_path(value, project_id):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\\" in value
        or re.search(r"%2f|%5c|%2e", value, re.I)
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        fail(400, "Use a valid request path inside this project.")
    # URL normalization must not let dot segments escape the selected project.
    url = httpx.URL(f"{_mock_base_url()}/mock/{project_id}{value}")
    if not url.path.startswith(f"/mock/{project_id}/") or any(
        part in {".", ".."} for part in urlsplit(value).path.split("/")
    ):
        fail(400, "Request path must stay inside this project.")
    return url


def file_path(path):
    reserved = {".reqlica", ".git", "drafts", "versions", "__pycache__", "project.json"}
    if (
        not path
        or len(path) > 1024
        or path.startswith("/")
        or re.match(r"[a-z]:", path, re.I)
        or re.search(r"[\\\x00-\x1f\x7f]", path)
        or any(
            not p
            or p in {".", ".."}
            or p in reserved
            or re.search(r"\.(sqlite|sqlite3|db|pyc)$|(-wal|-shm)$", p)
            for p in path.split("/")
        )
    ):
        fail(400, "Use a safe, editable project-relative source path.")


def prepare(route, method, body):
    """Keep UI confirmations and strip fields not accepted by domain schemas."""
    if (
        route in {"start", "stop", "activate", "registration", "logs/settings"}
        and method != "GET"
    ):
        if body.get("confirm") is not True:
            fail(400, "Explicit confirmation is required for this operation.")
    if route in {"start", "stop"}:
        return {}
    if route == "activate":
        if not revision(body.get("expected_revision")) or not re.fullmatch(
            ID, str(body.get("draft_id", ""))
        ):
            fail(
                400,
                "An exact draft revision and valid draft address are required for publication.",
            )
        return {key: body[key] for key in ("draft_id", "expected_revision")}
    if route == "registration":
        if type(body.get("delete_files")) is not bool or (
            body["delete_files"] and not isinstance(body.get("expected_directory"), str)
        ):
            fail(
                400, "Explicit project removal confirmation and directory are required."
            )
        return {k: body[k] for k in ("delete_files", "expected_directory") if k in body}
    if route == "logs/settings" and method == "PUT":
        return {k: v for k, v in body.items() if k != "confirm"}
    if re.fullmatch(rf"drafts/{ID}/files", route):
        if method != "GET" and not revision(body.get("expected_revision")):
            fail(400, "The exact inspected revision is required.")
        if method == "PUT":
            content = body.get("content")
            if not isinstance(content, str):
                fail(400, "File content is required.")
            if len(content.encode()) > 1024 * 1024:
                fail(413, "The draft source editor supports files up to 1 MiB.")
    if re.fullmatch(rf"drafts/{ID}/call", route):
        if not revision(body.get("expected_revision")) or body.get("method") not in {
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "HEAD",
        }:
            fail(400, "Use a current draft revision and a valid request method.")
        request_path(body.get("path"), "draft")
        if body.get("headers") is not None and not isinstance(body["headers"], dict):
            fail(400, "Request headers must be an object.")
        return {
            "expected_revision": body["expected_revision"],
            "method": body["method"],
            "path": body["path"],
            "json_body": body.get("json_body"),
            "headers": body.get("headers") or {},
        }
    if route.startswith("data/") and method == "POST":
        records = (
            body.get("records_by_table")
            if "/related/" in route
            else body.get("records")
        )
        pending = [records]
        while pending:
            value = pending.pop()
            if isinstance(value, (float, int)) and not isinstance(value, bool):
                if (isinstance(value, int) and abs(value) > 2**53 - 1) or (
                    isinstance(value, float)
                    and (
                        not math.isfinite(value)
                        or (value.is_integer() and abs(value) > 2**53 - 1)
                    )
                ):
                    fail(
                        400,
                        "Record numbers must be finite and integer IDs safely representable without rounding.",
                    )
            elif isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        if len(json.dumps(records, ensure_ascii=False).encode()) > 128 * 1024:
            fail(413, "The record batch exceeds 128 KiB.")
    return body


async def forward(
    request,
    path,
    *,
    runtime=False,
    body=None,
    raw=None,
    content_type="application/json",
):
    """Dispatch once, forwarding only the headers needed by the operation."""
    headers = {"Content-Type": content_type}
    if path.endswith("/step"):
        for name in ("x-reqlica-groq-key", "x-reqlica-activity-id"):
            if value := request.headers.get(name):
                if name.endswith("activity-id") and not re.fullmatch(ID, value):
                    fail(400, "Invalid activity ID.")
                headers[name] = value
    if path.endswith("/ai/models") or path == "/api/ai/models":
        if value := request.headers.get("x-reqlica-provider-key"):
            headers["X-Reqlica-Provider-Key"] = value
    if "/packages/" in path and request.method == "POST":
        headers["X-Reqlica-Package-Action"] = "reviewed-ui"
    transport = None if runtime else httpx.ASGITransport(app=request.app)
    origin = _mock_base_url() if runtime else "http://reqlica.internal"
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url=origin,
            trust_env=False,
            follow_redirects=False,
            timeout=620,
        ) as client:
            response = await client.request(
                request.method,
                path,
                headers=headers,
                content=raw
                if raw is not None
                else (json.dumps(body).encode() if body is not None else None),
            )
    except (httpx.HTTPError, OSError, ValueError, TimeoutError):
        return JSONResponse(
            {
                "error": "The response could not be confirmed. Refresh saved state before retrying; the operation may still finish."
            },
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )
    response_headers = {"Cache-Control": "no-store"}
    for name in ("content-type", "content-disposition"):
        if name in response.headers:
            response_headers[name] = response.headers[name]
    if not response.is_success:
        try:
            data = response.json()
            detail = data.get("detail", data.get("error"))
            if isinstance(detail, list):
                message = "; ".join(
                    f"{'.'.join(map(str, i.get('loc', [])))}: {i.get('msg', 'Invalid value')}"
                    for i in detail[:4]
                )[:1200]
            else:
                message = (
                    detail
                    if isinstance(detail, str)
                    else (detail or {}).get(
                        "message",
                        "Request rejected. Review the current state before retrying.",
                    )
                )
        except (ValueError, AttributeError, TypeError):
            message = "Request rejected. Review the current state before retrying."
        return JSONResponse(
            {"error": message},
            status_code=response.status_code,
            headers={"Cache-Control": "no-store"},
        )
    content = response.content
    if path.split("?")[0].endswith("/docs"):
        # Swagger must keep schema reads on the dashboard's same-origin adapter.
        content = re.sub(
            rb"url: '([^']+)'",
            lambda m: (
                b"url: '"
                + (
                    "/api/ui"
                    + urlsplit(m[1].decode()).path.removeprefix("/api")
                    + (
                        "?" + urlsplit(m[1].decode()).query
                        if urlsplit(m[1].decode()).query
                        else ""
                    )
                ).encode()
                + b"'"
            ),
            content,
        )
    if (
        re.fullmatch(rf"/api/projects/{ID}/drafts/{ID}/files", path.split("?")[0])
        and request.method == "GET"
    ):
        data = response.json()
        if (
            not isinstance(data.get("content"), str)
            or len(data["content"].encode()) > 1024 * 1024
        ):
            fail(
                413,
                "This file cannot be shown by the 1 MiB UTF-8 source editor. Use local tools.",
            )
    return Response(content, status_code=response.status_code, headers=response_headers)


@router.get("/connection")
async def connection(request: Request):
    result = {
        "console": str(request.base_url).rstrip("/"),
        "runtime": _mock_base_url(),
        "health": {},
    }
    if request.query_params.get("health") == "1":
        result["health"]["console"] = "Connected"
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=3, follow_redirects=False
            ) as client:
                response = await client.get(result["runtime"] + "/api/health")
            result["health"]["runtime"] = (
                "Connected" if response.is_success else f"HTTP {response.status_code}"
            )
        except httpx.HTTPError:
            result["health"]["runtime"] = "Unreachable"
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.api_route("/transfer", methods=["GET", "POST"])
async def transfer(request: Request):
    query = request.query_params
    action = query.get("action")
    importing = action in {"import", "workspace-import"}
    if action not in {"export", "workspace-export", "import", "workspace-import"}:
        fail(400, "Invalid transfer action.")
    if request.method != ("GET" if action == "export" else "POST"):
        fail(405, "Method not allowed.")
    same_origin(request)
    params = httpx.QueryParams()
    if importing:
        directory = query.get("directory", "")
        if not directory.startswith(("/", "~/")):
            fail(400, "Enter an absolute destination directory.")
        params = params.set("directory", directory)
        if action == "import":
            params = params.set(
                "clone", "true" if query.get("clone") == "true" else "false"
            )
            if query.get("name", "").strip():
                params = params.set("name", query["name"].strip())
        path = f"/api/projects/{action}?{params}"
        raw = await read_body(request, 32 * 1024 * 1024)
        return await forward(request, path, raw=raw, content_type="application/zip")
    project_id = query.get("projectId", "")
    if not re.fullmatch(ID, project_id):
        fail(400, "Select a project.")
    body = await json_body(request, 8192) if request.method == "POST" else None
    return await forward(request, f"/api/projects/{project_id}/{action}", body=body)


@router.api_route("/projects", methods=["GET", "POST"])
async def projects(request: Request):
    body = None
    if request.method == "POST":
        body = await json_body(request)
        if (
            not isinstance(body.get("name"), str)
            or not body["name"].strip()
            or not isinstance(body.get("directory"), str)
            or not body["directory"].strip().startswith(("/", "~/"))
        ):
            fail(400, "Enter a name and an absolute directory (or ~/ path).")
        body = {k: body[k].strip() for k in ("name", "directory")}
    return await forward(request, "/api/projects", runtime=True, body=body)


@router.api_route("/ai/{route:path}", methods=sorted(METHODS))
async def ai(request: Request, route: str):
    methods = {
        "key": {"PUT", "DELETE"},
        "key/status": {"POST"},
        "key/reveal": {"POST"},
        "models": {"POST"},
        "reasoning-options": {"POST"},
    }
    if request.method not in methods.get(route, set()):
        fail(405, "This AI operation is not available.")
    body = await json_body(request)
    return await forward(request, f"/api/ai/{route}", body=body)


@router.post("/projects/{project_id}/request")
async def test_request(request: Request, project_id: str):
    if not re.fullmatch(ID, project_id):
        fail(400, "Invalid project.")
    body = await json_body(request)
    method, path, rows = body.get("method"), body.get("path"), body.get("headers")
    if (
        method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
        or not isinstance(rows, list)
        or len(rows) > 100
    ):
        fail(400, "Invalid request parameters.")
    url = request_path(path, project_id)
    headers = {}
    forbidden = {
        "host",
        "cookie",
        "content-length",
        "connection",
        "origin",
        "referer",
        "transfer-encoding",
    }
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("value"), str)
            or type(row.get("enabled")) is not bool
        ):
            fail(400, "Invalid request headers.")
        if not row["enabled"] or not row["name"].strip():
            continue
        name, value = row["name"].strip().lower(), row["value"]
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", name) or re.search(
            r"[\r\n]", value
        ):
            fail(400, "Use valid header names and single-line values.")
        if name in forbidden or name.startswith(("sec-", "proxy-")):
            fail(400, f"The {name} header is managed by the connection.")
        if name in headers:
            fail(400, f"Remove the duplicate {name} header.")
        headers[name] = value
    start = time.monotonic()
    try:
        async with (
            asyncio.timeout(30),
            httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=30
            ) as client,
        ):
            async with client.stream(
                method,
                url,
                headers=headers,
                content=None
                if method in {"GET", "HEAD"}
                else str(body.get("body") or "").encode(),
            ) as response:
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > 1024 * 1024:
                        fail(502, "Response exceeds the 1 MiB preview limit.")
                    chunks.extend(chunk)
                result = {
                    "status": response.status_code,
                    "duration": round((time.monotonic() - start) * 1000),
                    "headers": dict(response.headers),
                    "body": chunks.decode("utf-8", errors="replace"),
                }
    except (httpx.HTTPError, OSError, ValueError, TimeoutError):
        fail(
            502,
            "No response confirmed. The request may have run. Check the runtime before resending.",
        )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.api_route("/projects/{project_id}/{route:path}", methods=sorted(METHODS))
async def project_operation(request: Request, project_id: str, route: str):
    if not re.fullmatch(ID, project_id):
        fail(400, "Invalid project address.")
    file = bool(re.fullmatch(rf"drafts/{ID}/files", route))
    data = route in {
        "data/schema",
        "data/validate",
        "data/insert",
        "data/related/validate",
        "data/related/insert",
    }
    permitted = (
        request.method in {"GET", "PUT", "DELETE"}
        if file
        else request.method == ("GET" if route == "data/schema" else "POST")
        if data
        else allowed(request.method, route)
    )
    if not permitted:
        fail(405, "This operation is not available.")
    if file:
        file_path(request.query_params.get("path", ""))
    limit = 2 * 1024 * 1024 if file else 256 * 1024 if data else 1024 * 1024
    body = await json_body(request, limit) if request.method != "GET" else None
    if body is not None:
        body = prepare(route, request.method, body)
    upstream = (
        ""
        if route == "registration"
        else "/" + ("deletion" if route == "deletion-files" else route)
    )
    path = f"/api/projects/{project_id}{upstream}"
    # Query values remain encoded, never interpreted as route segments.
    query = request.query_params
    names = (
        ["path"]
        if file
        else ["limit", "before_id", "kind", "request_id"]
        if route == "logs"
        else ["offset", "limit"]
        if route == "packages/jobs"
        else ["revision", "format", "draft_id"]
        if route == "schema-export"
        else ["revision"]
        if route.endswith("openapi.json")
        else []
    )
    params = httpx.QueryParams(
        [(name, value) for name in names for value in query.getlist(name)]
    )
    for name in names:
        values = query.getlist(name)
        if len(values) > 1:
            fail(400, f"Duplicate query parameter: {name}.")
        if values and name in {"limit", "before_id", "offset"}:
            value = values[0]
            if not re.fullmatch(r"[0-9]{1,16}", value) or int(value) > 2**53 - 1:
                fail(400, f"Invalid history filter: {name}.")
    if re.fullmatch(r"logs/[1-9]\d*", route) and int(route.split("/")[1]) > 2**53 - 1:
        fail(400, "Invalid event ID.")
    if route == "deletion-files":
        params = params.set("delete_files", "true")
    if params:
        path += "?" + str(params)
    return await forward(request, path, runtime=route in RUNTIME, body=body)
