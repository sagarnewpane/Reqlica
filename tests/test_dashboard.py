"""Dashboard packaging and server adapters, independent of a Node.js server."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from reqlica.console.app import create_app
from reqlica.console.dashboard_api import allowed
from reqlica.projects.store import create_project


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "REQLICA_HOME": str(self.directory / "home"),
                    "MOCK_PORT": "4000",
                    "MOCK_HOST": "127.0.0.1",
                },
            )
        )
        self.project = create_project(self.directory / "project")
        self.assets = self.directory / "assets"
        self.assets.mkdir()
        for name in ("index", "project", "settings", "404"):
            (self.assets / f"{name}.html").write_text(
                f"<!doctype html><title>{name}</title>"
            )
        (self.assets / "_next/static").mkdir(parents=True)
        (self.assets / "_next/static/app.js").write_text("console.log('dashboard')")
        self.client = self.enterContext(
            TestClient(
                create_app(frontend_directory=self.assets),
                base_url="http://127.0.0.1:3000",
            )
        )
        self.root = f"/api/ui/projects/{self.project.id}"
        self.calls = []
        self.upstream = lambda request: httpx.Response(200, json={"status": "ok"})
        self.original_client = httpx.AsyncClient
        # Mock only external gateway HTTP, leaving console ASGI dispatch real.
        self.enterContext(
            patch(
                "reqlica.console.dashboard_api.httpx.AsyncClient",
                side_effect=self.http_client,
            )
        )

    def http_client(self, **kwargs):
        if not kwargs.get("transport"):
            kwargs["transport"] = httpx.MockTransport(self.external)
        return self.original_client(**kwargs)

    def external(self, request):
        self.calls.append(request)
        return self.upstream(request)

    def request(self, method, path, **kwargs):
        return self.client.request(method, self.root + "/" + path, **kwargs)

    def draft(self):
        response = self.request("POST", "drafts", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_static_pages_assets_and_unknown_routes(self):
        for path in (
            "/",
            "/project?projectId=" + self.project.id,
            "/settings",
            "/settings.html",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("text/html", response.headers["content-type"])
            self.assertEqual(response.headers["cache-control"], "no-cache")
        response = self.client.get("/_next/static/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("immutable", response.headers["cache-control"])
        self.assertEqual(self.client.head("/project").content, b"")
        for path in (
            "/missing",
            "/api/missing",
            "/_next/missing.js",
            "/%2e%2e/project.json",
        ):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.post("/project").status_code, 405)
        self.assertEqual(
            self.client.get("/api/health").json()["service"], "reqlica-console"
        )

    def test_missing_assets_have_actionable_error_and_api_still_works(self):
        with TestClient(
            create_app(frontend_directory=self.directory / "missing")
        ) as client:
            self.assertEqual(client.get("/").status_code, 503)
            self.assertIn("build_frontend.py", client.get("/").text)
            self.assertEqual(client.get("/api/health").status_code, 200)

    def test_legacy_project_links_redirect_without_losing_draft_or_prompt(self):
        response = self.client.get(
            f"/projects/{self.project.id}?draftId=saved&prompt=hello",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            f"/project?draftId=saved&prompt=hello&projectId={self.project.id}",
        )

    def test_mcp_remains_available(self):
        response = self.client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "dashboard-test", "version": "1"},
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("result", response.json())

    def test_project_list_creation_and_conflict_forward_once(self):
        self.upstream = lambda request: httpx.Response(
            201 if request.method == "POST" else 200, json={"projects": []}
        )
        response = self.client.get("/api/ui/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        response = self.client.post(
            "/api/ui/projects", json={"name": " Books ", "directory": " ~/books "}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            json.loads(self.calls[-1].content),
            {"name": "Books", "directory": "~/books"},
        )
        self.assertEqual(str(self.calls[-1].url), "http://127.0.0.1:4000/api/projects")
        self.upstream = lambda request: httpx.Response(
            409, json={"detail": "Directory must be empty"}
        )
        self.assertEqual(
            self.client.post(
                "/api/ui/projects", json={"name": "Books", "directory": "/tmp/books"}
            ).json()["error"],
            "Directory must be empty",
        )

    def test_same_origin_and_json_guards_before_mutation(self):
        for headers, status in (
            ({"Origin": "https://elsewhere.example"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Origin": "null"}, 403),
            ({"content-type": "text/plain"}, 415),
        ):
            response = self.client.post(
                "/api/ui/projects",
                content='{"name":"Books","directory":"/tmp/books"}',
                headers={"content-type": "application/json", **headers},
            )
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.calls, [])
        response = self.client.post(
            "/api/ui/projects",
            content="[]",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_worker_and_publication_confirmations_and_payloads(self):
        for action in ("start", "stop", "activate", "registration"):
            method = "DELETE" if action == "registration" else "POST"
            for confirm in (None, False, "true", 1):
                response = self.request(method, action, json={"confirm": confirm})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.calls, [])
        for action in ("start", "stop"):
            response = self.request(
                "POST",
                action,
                json={
                    "confirm": True,
                    "api_key": "not-forwarded",
                    "delete_files": True,
                },
                headers={"X-Reqlica-Groq-Key": "not-forwarded"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(self.calls[-1].content), {})
            self.assertNotIn("x-reqlica-groq-key", self.calls[-1].headers)
        body = {"confirm": True, "expected_revision": "c" * 64, "draft_id": "b" * 32}
        self.assertEqual(self.request("POST", "activate", json=body).status_code, 200)
        self.assertEqual(
            json.loads(self.calls[-1].content),
            {k: body[k] for k in ("expected_revision", "draft_id")},
        )
        self.assertEqual(
            self.request(
                "DELETE", "registration", json={"confirm": True, "delete_files": False}
            ).status_code,
            200,
        )
        self.assertEqual(self.calls[-1].url.path, f"/api/projects/{self.project.id}")
        self.assertEqual(json.loads(self.calls[-1].content), {"delete_files": False})
        self.request("GET", "deletion-files")
        self.assertEqual(self.calls[-1].url.query, b"delete_files=true")

    def test_source_edit_conflicts_and_safe_paths(self):
        draft = self.draft()
        route = f"drafts/{draft['id']}/files"
        response = self.request(
            "PUT",
            route,
            params={"path": "notes/a & b.txt"},
            json={"content": "", "expected_revision": draft["revision"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        read = self.request("GET", route, params={"path": "notes/a & b.txt"})
        self.assertEqual(read.json()["content"], "")
        stale = self.request(
            "PUT",
            route,
            params={"path": "notes/a & b.txt"},
            json={"content": "stale", "expected_revision": draft["revision"]},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertIn("error", stale.json())
        for path in (
            "../project.json",
            "/tmp/file",
            ".reqlica/secret",
            "data.sqlite",
            "endpoints/../x.py",
            "C:/file",
            "a\\b",
            "a//b",
            "__pycache__/x",
        ):
            self.assertEqual(
                self.request("GET", route, params={"path": path}).status_code, 400, path
            )
        response = self.request(
            "PUT",
            route,
            params={"path": "big.txt"},
            json={
                "content": "x" * (1024 * 1024 + 1),
                "expected_revision": read.json()["revision"],
            },
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            self.request(
                "DELETE",
                route,
                params={"path": "notes/a & b.txt"},
                json={"expected_revision": read.json()["revision"]},
            ).status_code,
            200,
        )

    def test_adapter_allowlist_rejects_unknown_operations_and_methods(self):
        for route in (
            "publish",
            f"drafts/{'b' * 32}/publish",
            "environment/INVALID-NAME",
            "agent-runs/invalid",
            "logs/0",
            "logs/arbitrary",
        ):
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                self.assertFalse(allowed(method, route), (method, route))
                self.assertEqual(self.request(method, route, json={}).status_code, 405)
        for route, methods in (
            ("drafts", {"GET", "POST"}),
            ("start", {"POST"}),
            (f"drafts/{'b' * 32}", {"GET", "PATCH", "DELETE"}),
            ("logs", {"GET"}),
        ):
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                self.assertEqual(
                    allowed(method, route), method in methods, (method, route)
                )

    def test_real_console_history_capture_and_revision_errors(self):
        response = self.request("GET", "logs", params={"limit": 10})
        self.assertEqual(response.status_code, 200, response.text)
        for params in ({"limit": 101}, {"kind": "wrong"}, {"request_id": "invalid"}):
            self.assertEqual(
                self.request("GET", "logs", params=params).status_code, 422
            )
        settings = self.request("GET", "logs/settings").json()
        body = {
            "confirm": True,
            "expected_revision": settings["revision"],
            "capture_payloads": True,
            "capture_output": False,
            "max_body_bytes": 512,
        }
        self.assertEqual(
            self.request("PUT", "logs/settings", json=body).status_code, 200
        )
        self.assertEqual(
            self.request("PUT", "logs/settings", json=body).status_code, 409
        )
        self.assertEqual(self.calls, [])

    def test_schema_and_swagger_use_browser_visible_addresses(self):
        self.upstream = lambda request: httpx.Response(
            200,
            text="url: 'http://127.0.0.1:4000/api/projects/"
            + self.project.id
            + "/openapi.json?revision=abc'",
            headers={"content-type": "text/html"},
        )
        response = self.request("GET", "docs")
        self.assertIn(self.root + "/openapi.json?revision=abc", response.text)
        self.assertNotIn("127.0.0.1:4000", response.text)

    def test_ai_discovery_credentials_scope_and_saved_key_operations(self):
        body = {"provider": "groq", "base_url": None, "api_key": "test-key"}
        self.assertEqual(self.client.put("/api/ui/ai/key", json=body).status_code, 200)
        settings = {"provider": "groq", "base_url": None}
        self.assertTrue(
            self.client.post("/api/ui/ai/key/status", json=settings).json()[
                "configured"
            ]
        )
        self.assertNotIn(
            "test-key", self.client.post("/api/ui/ai/key/status", json=settings).text
        )
        self.assertEqual(
            self.client.post("/api/ui/ai/key/reveal", json=settings).json()["api_key"],
            "test-key",
        )
        self.assertEqual(
            self.client.request("DELETE", "/api/ui/ai/key", json=settings).status_code,
            200,
        )
        self.assertEqual(self.calls, [])

    def test_request_tester_limits_paths_headers_redirects_and_no_retry(self):
        body = {
            "method": "POST",
            "path": "/books?x=1",
            "headers": [
                {"name": "Authorization", "value": "Bearer test", "enabled": True}
            ],
            "body": '{"title":"A"}',
        }
        self.upstream = lambda request: httpx.Response(
            302, headers={"location": "https://elsewhere.example"}, content=b"redirect"
        )
        response = self.request("POST", "request", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], 302)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].headers["authorization"], "Bearer test")
        self.assertEqual(self.calls[0].url.path, f"/mock/{self.project.id}/books")
        for path in (
            "/../other",
            "/%2e%2e/other",
            "/%2fother",
            "/x\\other",
            "https://elsewhere.example",
        ):
            self.assertEqual(
                self.request(
                    "POST", "request", json={**body, "path": path}
                ).status_code,
                400,
            )
        for name in (
            "Host",
            "Cookie",
            "Sec-Fetch-Site",
            "Proxy-Authorization",
            "Content-Length",
        ):
            response = self.request(
                "POST",
                "request",
                json={
                    **body,
                    "headers": [{"name": name, "value": "bad", "enabled": True}],
                },
            )
            self.assertEqual(response.status_code, 400)
        self.upstream = lambda request: httpx.Response(
            200, content=b"x" * (1024 * 1024 + 1)
        )
        self.assertEqual(self.request("POST", "request", json=body).status_code, 502)
        self.upstream = lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("unreachable")
        )
        count = len(self.calls)
        response = self.request("POST", "request", json=body)
        self.assertEqual(response.status_code, 502)
        self.assertIn("may have run", response.json()["error"])
        self.assertEqual(len(self.calls), count + 1)

    def test_transfers_preserve_zip_bytes_and_validate_destinations(self):
        response = self.client.get(
            f"/api/ui/transfer?action=export&projectId={self.project.id}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content[:2], b"PK")
        self.assertEqual(response.headers["content-type"], "application/zip")
        imported = self.client.post(
            "/api/ui/transfer",
            params={
                "action": "import",
                "directory": str(self.directory / "clone"),
                "clone": "true",
            },
            content=response.content,
            headers={"Content-Type": "application/zip"},
        )
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertNotEqual(imported.json()["project"]["id"], self.project.id)
        self.assertEqual(
            self.client.post(
                "/api/ui/transfer?action=import&directory=relative", content=b"PK"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/ui/transfer?action=import&directory=/tmp/unused",
                content=b"x" * (32 * 1024 * 1024 + 1),
            ).status_code,
            413,
        )

    def test_data_validation_and_insertion_consent_remain_enforced(self):
        body = {"target": "json", "records": [{"id": 1, "title": "A"}]}
        response = self.request("POST", "data/validate", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        for records in ([{}], [], [{"id": 2**63}], [{"n": float("inf")}]):
            response = self.request(
                "POST",
                "data/validate",
                content=json.dumps({**body, "records": records}),
                headers={"content-type": "application/json"},
            )
            self.assertIn(response.status_code, {400, 422}, response.text)
        self.assertEqual(
            self.request("POST", "data/insert", json=body).status_code, 422
        )
        self.assertEqual(self.calls, [])

    def test_uncertain_management_response_is_never_retried(self):
        self.upstream = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("lost"))
        response = self.request("POST", "start", json={"confirm": True})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("may still finish", response.json()["error"])

    def test_connection_uses_hosting_console_and_runtime_configuration(self):
        response = self.client.get("/api/ui/connection?health=1")
        self.assertEqual(response.json()["console"], "http://127.0.0.1:3000")
        self.assertEqual(response.json()["runtime"], "http://127.0.0.1:4000")
        self.assertEqual(response.json()["health"]["console"], "Connected")
        self.assertEqual(
            self.client.put("/api/ui/connection", json={}).status_code, 404
        )


class DashboardDispatchTests(unittest.TestCase):
    """Verify adapter metadata against a capture endpoint without model calls."""

    def setUp(self):
        from reqlica.console.dashboard_api import router

        app = FastAPI()
        app.include_router(router)
        self.received = []

        @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
        async def capture(request: Request, path: str):
            self.received.append(request)
            raw = await request.body()
            return {
                "headers": dict(request.headers),
                "body": json.loads(raw) if raw else None,
                "query": str(request.query_params),
            }

        self.client = self.enterContext(TestClient(app))
        self.root = "/api/ui/projects/" + "a" * 32
        self.run = "agent-runs/" + "b" * 32

    def test_model_step_alone_receives_legacy_credentials_and_activity_id(self):
        headers = {
            "X-Reqlica-Groq-Key": "request-only",
            "X-Reqlica-Activity-ID": "c" * 32,
            "X-Reqlica-Provider-Key": "not-a-step-key",
        }
        for route in (self.run, self.run + "/context", self.run + "/step"):
            response = self.client.request(
                "POST" if route.endswith("/step") else "GET",
                self.root + "/" + route,
                headers=headers,
                json={},
            )
            self.assertEqual(response.status_code, 200, response.text)
            forwarded = response.json()["headers"]
            self.assertEqual(
                forwarded.get("x-reqlica-groq-key"),
                "request-only" if route.endswith("/step") else None,
            )
            self.assertEqual(
                forwarded.get("x-reqlica-activity-id"),
                "c" * 32 if route.endswith("/step") else None,
            )
            self.assertNotIn("x-reqlica-provider-key", forwarded)
        before = len(self.received)
        response = self.client.post(
            self.root + "/" + self.run + "/step",
            json={},
            headers={**headers, "X-Reqlica-Activity-ID": "invalid"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.received), before)

    def test_discovery_and_package_headers_stay_scoped(self):
        headers = {
            "X-Reqlica-Provider-Key": "discovery-only",
            "X-Reqlica-Groq-Key": "never",
        }
        response = self.client.post(
            "/api/ui/ai/models", json={"provider": "groq"}, headers=headers
        )
        self.assertEqual(
            response.json()["headers"]["x-reqlica-provider-key"], "discovery-only"
        )
        self.assertNotIn("x-reqlica-groq-key", response.json()["headers"])
        response = self.client.post(
            self.root + "/packages/preview",
            json={"package": "example"},
            headers=headers,
        )
        self.assertEqual(
            response.json()["headers"]["x-reqlica-package-action"], "reviewed-ui"
        )
        self.assertNotIn("x-reqlica-provider-key", response.json()["headers"])

    def test_draft_calls_preserve_exact_revision_and_supported_fields(self):
        body = {
            "expected_revision": "c" * 64,
            "method": "GET",
            "path": "/hello?name=A",
            "headers": {},
            "json_body": None,
            "unrelated": "discard",
        }
        response = self.client.post(
            self.root + "/drafts/" + "b" * 32 + "/call", json=body
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["body"], {k: v for k, v in body.items() if k != "unrelated"}
        )
