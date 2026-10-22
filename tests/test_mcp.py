"""Exercise AI tool discovery and draft generation over the console's HTTP MCP."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from reqlica.console.app import create_app
from reqlica.projects.store import create_project, get_project, registry_directory
from reqlica.projects.versions import code_directory, content_digest


class MCPTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "books", "Book API")
        self.client = self.enterContext(
            TestClient(create_app(), base_url="http://127.0.0.1:3000")
        )
        self.request_id = 0
        initialized = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "reqlica-tests", "version": "1"},
            },
        )
        self.assertEqual(initialized["serverInfo"]["name"], "Reqlica")
        self.instructions = initialized["instructions"]
        response = self.client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        self.assertEqual(response.status_code, 202)

    def rpc(self, method, params=None):
        self.request_id += 1
        response = self.client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": method,
                "params": params or {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], self.request_id)
        self.assertNotIn("error", payload, payload)
        return payload["result"]

    def tool(self, name, arguments=None):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        self.assertFalse(result.get("isError"), result)
        return json.loads(result["content"][0]["text"])

    def tool_error(self, name, arguments, message):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        self.assertTrue(result["isError"], result)
        self.assertIn(message, result["content"][0]["text"])

    def create_draft(self):
        return self.tool("create_draft", {"project_id": self.project.id})

    def test_discovery_schemas_and_no_ai_approval_or_activation(self):
        tools = {tool["name"]: tool for tool in self.rpc("tools/list")["tools"]}
        self.assertEqual(
            set(tools),
            {
                "list_projects",
                "list_drafts",
                "get_project_context",
                "create_draft",
                "write_draft_file",
                "upsert_endpoint",
                "remove_endpoint",
                "get_draft_changes",
                "validate_draft",
                "test_draft",
                "call_draft_endpoint",
                "get_project_urls",
                "get_draft_urls",
                "get_api_schema",
                "get_mock_data_context",
                "preview_mock_data",
            },
        )
        self.assertTrue(tools["get_project_context"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["test_draft"]["annotations"]["openWorldHint"])
        convenience = {"get_project_urls", "get_draft_urls"}
        for name, tool in tools.items():
            self.assertEqual(
                tool["_meta"]["reqlica/category"],
                "convenience" if name in convenience else "core",
            )
        for name in convenience:
            self.assertNotIn(
                "expected_revision", tools[name]["inputSchema"]["properties"]
            )
            self.assertIn("Convenience only", tools[name]["description"])
        schema = tools["write_draft_file"]["inputSchema"]
        self.assertIn("expected_revision", schema["required"])
        self.assertEqual(
            schema["properties"]["expected_revision"]["pattern"], "^[0-9a-f]{64}$"
        )
        for name in ("approve_draft", "activate_draft"):
            self.tool_error(name, {}, "Unknown tool")
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)

    def test_mock_data_tools_validate_json_and_reject_unsafe_or_stale_input(self):
        arguments = {"project_id": self.project.id}
        preview = self.tool("preview_mock_data", {**arguments, "records_json": '[{"name":"Example"}]'})
        self.assertEqual(preview["validation"]["status"], "passed")
        for records in ['[{"name":"first","name":"second"}]', '[{"value":NaN}]', '[{"value":Infinity}]']:
            self.tool_error("preview_mock_data", {**arguments, "records_json": records}, "Error")
        self.tool_error("preview_mock_data", {**arguments, "target": "sqlite", "table": "users",
            "expected_schema": "f" * 64, "records_json": '[{"name":"Example"}]'}, "schema changed")
        context = self.tool("get_mock_data_context", arguments)
        self.assertIn("fingerprint", context["schema"])
        self.assertIn("application", context)
        self.tool_error("preview_mock_data", {**arguments, "target": "related", "table": "users",
            "expected_schema": "f" * 64, "records_json": '{"users":[{"name":"Example"}]}'}, "not table")

    def test_context_reads_source_without_importing_and_lists_broken_projects(self):
        active = code_directory(self.project)
        handler = active / "endpoints/hello.py"
        handler.write_text("raise RuntimeError('must not import')\n")
        (active / "asset.bin").write_bytes(b"\xff\xfe")
        context = self.tool("get_project_context", {"project_id": self.project.id})
        self.assertEqual(context["project"]["id"], self.project.id)
        self.assertEqual(context["source"]["endpoints/hello.py"], handler.read_text())
        self.assertEqual(context["binary_files"], ["asset.bin"])
        self.assertNotIn("data.sqlite", context["files"])
        self.assertNotIn("project.json", context["files"])
        self.assertIsNone(context["draft"])
        self.assertEqual(context["endpoints"][0]["path"], "/hello")
        self.assertIn("reqlica.runtime.sdk.transaction", context["helpers"])
        broken_id = "f" * 32
        (registry_directory() / f"{broken_id}.json").write_text("not json")
        projects = self.tool("list_projects")["projects"]
        indexed = {project["id"]: project for project in projects}
        self.assertEqual(indexed[self.project.id]["name"], "Book API")
        self.assertIsNone(indexed[self.project.id]["error"])
        self.assertIn("Cannot open project", indexed[broken_id]["error"])

    def test_convenience_url_tools_keep_draft_and_published_urls_separate(self):
        draft = self.create_draft()
        with (
            patch("reqlica.cli.load_dotenv"),
            patch.dict(
                os.environ,
                {
                    "MOCK_HOST": "::",
                    "MOCK_PORT": "4100",
                    "CONSOLE_HOST": "0.0.0.0",
                    "CONSOLE_PORT": "3100",
                },
            ),
        ):
            published = self.tool(
                "get_project_urls", {"project_id": self.project.id, "path": "/hello"}
            )
            preview = self.tool(
                "get_draft_urls",
                {
                    "project_id": self.project.id,
                    "draft_id": draft["id"],
                    "path": "/hello",
                },
            )
        self.assertEqual(published["kind"], "published")
        self.assertEqual(
            published["endpoints"][0]["url"],
            f"http://[::1]:4100/mock/{self.project.id}/hello",
        )
        self.assertEqual(preview["kind"], "draft_preview")
        self.assertEqual(preview["revision"], draft["revision"])
        self.assertTrue(preview["docs_url"].endswith(f"/{draft['id']}/docs"))
        self.assertTrue(preview["openapi_url"].endswith(f"/{draft['id']}/openapi.json"))
        self.assertEqual(
            preview["endpoints"][0]["url"],
            f"http://127.0.0.1:3100/api/projects/{self.project.id}/drafts/{draft['id']}/preview/hello",
        )
        response = self.client.get(preview["endpoints"][0]["url"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["project_id"], self.project.id)
        self.assertEqual(
            response.headers["x-reqlica-draft-revision"], draft["revision"]
        )
        state = self.tool(
            "get_draft_changes",
            {"project_id": self.project.id, "draft_id": draft["id"]},
        )
        self.assertIsNone(state["validation"])
        self.assertIsNone(state["test"])
        self.assertIsNone(state["approval"])
        self.tool_error(
            "get_draft_urls",
            {
                "project_id": self.project.id,
                "draft_id": draft["id"],
                "path": "/missing",
            },
            "No endpoint",
        )

    def test_initialize_teaches_identifier_sources_workflow_and_recovery(self):
        instructions = " ".join(self.instructions.split())
        for guidance in (
            "list_projects.projects[].id",
            "list_drafts",
            "create_draft's top-level 'id'",
            "There is no create_project tool",
            "create_draft ONCE",
            "write_draft_file",
            "upsert_endpoint",
            "validate_draft",
            "test_draft",
            "get_draft_changes",
            "get_project_context",
            "call_draft_endpoint",
            "get_api_schema",
            "Do not guess IDs/hashes",
            "sequentially, not in parallel",
            "On a revision conflict",
            "There are NO approval or activation tools",
            "POST then GET in separate calls is NOT a persistent session",
            "NOT a sandbox",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, instructions)

    def test_schema_tool_reads_validated_contracts_and_reports_stale_artifacts(self):
        draft = self.create_draft()
        ids = {"project_id": self.project.id, "draft_id": draft["id"]}
        self.tool_error("get_api_schema", ids, "Run validate_draft")
        validation = self.tool(
            "validate_draft", {**ids, "expected_revision": draft["revision"]}
        )
        self.assertEqual(validation["status"], "passed", validation)
        result = self.tool("get_api_schema", {**ids, "method": "GET", "path": "/hello"})
        self.assertEqual(result["revision"], draft["revision"])
        self.assertEqual(set(result["schema"]["paths"]), {"/hello"})
        self.assertIn("get", result["schema"]["paths"]["/hello"])
        self.assertEqual(result["contract_status"], "incomplete")
        self.assertTrue(result["warnings"])
        self.tool_error("get_api_schema", {**ids, "method": "GET"}, "together")
        self.tool_error(
            "get_api_schema",
            {**ids, "method": "GET", "path": "/missing"},
            "No endpoint schema",
        )
        self.tool(
            "write_draft_file",
            {
                **ids,
                "expected_revision": draft["revision"],
                "path": "notes.txt",
                "content": "changed",
            },
        )
        self.tool_error("get_api_schema", ids, "missing or stale")

    def test_fresh_session_can_discover_drafts_and_request_focused_context(self):
        first = self.create_draft()
        second = self.create_draft()
        second = self.tool(
            "write_draft_file",
            {
                "project_id": self.project.id,
                "draft_id": second["id"],
                "expected_revision": second["revision"],
                "path": "PROJECT_CONTEXT.md",
                "content": "Intent: implement named greetings. Pending: validation/tests.",
            },
        )
        page = self.tool("list_drafts", {"project_id": self.project.id, "limit": 1})
        self.assertEqual(page["total"], 2)
        self.assertIsNotNone(page["next_offset"])
        rest = self.tool(
            "list_drafts",
            {"project_id": self.project.id, "limit": 1, "offset": page["next_offset"]},
        )
        self.assertEqual(
            {item["id"] for item in page["drafts"] + rest["drafts"]},
            {first["id"], second["id"]},
        )
        self.assertTrue(
            all(
                item["progress"] == "editing"
                for item in page["drafts"] + rest["drafts"]
            )
        )
        self.assertIsNone(rest["next_offset"])
        context = self.tool(
            "get_project_context",
            {
                "project_id": self.project.id,
                "draft_id": second["id"],
                "include_source": False,
            },
        )
        self.assertEqual(context["source"], {})
        self.assertEqual(context["draft_index"]["total"], 2)
        self.assertEqual(context["database"]["status"], "available")
        self.assertIn("named greetings", context["project_notes"]["content"])
        self.assertEqual(context["handoff"]["selected_revision"], second["revision"])
        focused = self.tool(
            "get_project_context",
            {
                "project_id": self.project.id,
                "draft_id": second["id"],
                "source_paths": ["PROJECT_CONTEXT.md"],
                "include_database": False,
            },
        )
        self.assertEqual(set(focused["source"]), {"PROJECT_CONTEXT.md"})
        self.assertEqual(focused["database"]["status"], "not_requested")

    def test_discovered_parameters_explain_inputs_and_nested_route_fields(self):
        tools = {tool["name"]: tool for tool in self.rpc("tools/list")["tools"]}
        context_parameters = tools["get_project_context"]["inputSchema"]["properties"]
        self.assertTrue(context_parameters["include_source"]["default"])
        self.assertTrue(context_parameters["include_database"]["default"])
        for name in ("source_paths", "database_tables"):
            array_schema = next(
                item
                for item in context_parameters[name]["anyOf"]
                if item.get("type") == "array"
            )
            self.assertEqual(array_schema["maxItems"], 50)
        draft_parameters = tools["list_drafts"]["inputSchema"]["properties"]
        self.assertEqual(draft_parameters["limit"]["maximum"], 100)
        self.assertEqual(draft_parameters["offset"]["minimum"], 0)
        for name, tool in tools.items():
            for parameter, schema in tool["inputSchema"]["properties"].items():
                with self.subTest(tool=name, parameter=parameter):
                    self.assertTrue(schema.get("description"), schema)
            if "draft_id" in tool["inputSchema"]["properties"]:
                description = tool["inputSchema"]["properties"]["draft_id"][
                    "description"
                ]
                self.assertIn("create_draft", description)
        endpoint = tools["upsert_endpoint"]["inputSchema"]["$defs"]["Endpoint"]
        self.assertEqual(set(endpoint["required"]), {"method", "path", "handler"})
        for parameter in endpoint["required"]:
            self.assertTrue(endpoint["properties"][parameter]["description"])
            self.assertTrue(endpoint["properties"][parameter]["examples"])
        self.assertIn("/books/{book_id}", endpoint["properties"]["path"]["examples"])
        self.assertIn(
            "endpoints/books.py:list_books",
            endpoint["properties"]["handler"]["examples"],
        )
        request_path = tools["call_draft_endpoint"]["inputSchema"]["properties"]["path"]
        self.assertIn("/books/123", request_path["examples"])
        self.assertIn("complete", tools["write_draft_file"]["description"])
        self.assertIn("'id', NOT 'draft_id'", tools["create_draft"]["description"])
        self.assertIn("does NOT", tools["write_draft_file"]["description"])
        self.assertIn("status_code", tools["call_draft_endpoint"]["description"])

    def test_route_registration_does_not_write_handler_source(self):
        draft = self.create_draft()
        ids = {"project_id": self.project.id, "draft_id": draft["id"]}
        draft = self.tool(
            "upsert_endpoint",
            {
                **ids,
                "expected_revision": draft["revision"],
                "endpoint": {
                    "method": "GET",
                    "path": "/books",
                    "handler": "endpoints/books.py:list_books",
                },
            },
        )
        self.assertNotIn("endpoints/books.py", draft["files"])
        result = self.tool(
            "validate_draft", {**ids, "expected_revision": draft["revision"]}
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("Missing Python handler", result["output"])
        draft = self.tool(
            "write_draft_file",
            {
                **ids,
                "expected_revision": draft["revision"],
                "path": "endpoints/books.py",
                "content": "def list_books():\n    return []\n",
            },
        )
        result = self.tool(
            "validate_draft", {**ids, "expected_revision": draft["revision"]}
        )
        self.assertEqual(result["status"], "passed", result)

    def test_ai_can_create_edit_check_and_call_an_endpoint_without_publishing(self):
        active = code_directory(self.project)
        active_revision = content_digest(active)
        draft = self.create_draft()
        for path, content in (
            (
                "endpoints/echo.py",
                "def echo(body: dict) -> dict:\n    return {'received': body}\n",
            ),
            (
                "tests/test_echo.py",
                (
                    "import unittest\nfrom endpoints.echo import echo\n\n"
                    "class EchoTests(unittest.TestCase):\n"
                    "    def test_echo(self):\n"
                    "        self.assertEqual(echo({'value': 1}), {'received': {'value': 1}})\n"
                ),
            ),
        ):
            draft = self.tool(
                "write_draft_file",
                {
                    "project_id": self.project.id,
                    "draft_id": draft["id"],
                    "expected_revision": draft["revision"],
                    "path": path,
                    "content": content,
                },
            )
        unregistered = self.tool(
            "get_project_context",
            {"project_id": self.project.id, "draft_id": draft["id"]},
        )
        self.assertIn("endpoints/echo.py", unregistered["source"])
        self.assertNotIn(
            "/echo", {route["path"] for route in unregistered["endpoints"]}
        )
        draft = self.tool(
            "upsert_endpoint",
            {
                "project_id": self.project.id,
                "draft_id": draft["id"],
                "expected_revision": draft["revision"],
                "endpoint": {
                    "method": "POST",
                    "path": "/echo",
                    "handler": "endpoints/echo.py:echo",
                },
            },
        )
        ids = {"project_id": self.project.id, "draft_id": draft["id"]}
        revision = {**ids, "expected_revision": draft["revision"]}
        context = self.tool("get_project_context", ids)
        self.assertEqual(context["revision"], draft["revision"])
        self.assertIn("endpoints/echo.py", context["source"])
        self.assertEqual(len(context["endpoints"]), 2)
        for name in ("validate_draft", "test_draft"):
            result = self.tool(name, revision)
            self.assertEqual(result["status"], "passed", result)
            self.assertEqual(result["revision"], draft["revision"])
        called = self.tool(
            "call_draft_endpoint",
            {**revision, "method": "POST", "path": "/echo", "json_body": {"value": 7}},
        )
        self.assertEqual(called["status_code"], 200, called)
        self.assertEqual(called["body"], {"received": {"value": 7}})
        changes = self.tool("get_draft_changes", ids)
        self.assertEqual(changes["changes"]["modified"], ["manifest.json"])
        self.assertEqual(changes["validation"]["status"], "passed")
        self.assertGreaterEqual(changes["test"]["tests_passed"], 2)
        self.assertIsNone(changes["approval"])
        removed = self.tool(
            "remove_endpoint", {**revision, "method": "POST", "path": "/echo"}
        )
        self.assertIsNone(removed["validation"])
        self.assertIsNone(removed["test"])
        self.assertIn("endpoints/echo.py", removed["files"])
        self.assertEqual(content_digest(active), active_revision)
        self.assertEqual(
            get_project(self.project.id).active_version, self.project.active_version
        )

    def test_invalid_input_conflicts_and_unsafe_paths_are_tool_errors(self):
        self.tool_error(
            "get_project_context", {"project_id": "f" * 32}, "Unknown project"
        )
        self.tool_error("create_draft", {"project_id": "../outside"}, "pattern")
        draft = self.create_draft()
        arguments = {
            "project_id": self.project.id,
            "draft_id": draft["id"],
            "expected_revision": draft["revision"],
            "path": "notes.txt",
            "content": "new",
        }
        for path in ("../outside.txt", "/absolute.txt", "project.json", "data.sqlite"):
            self.tool_error("write_draft_file", {**arguments, "path": path}, "path")
        self.tool_error(
            "write_draft_file", {**arguments, "expected_revision": "bad"}, "pattern"
        )
        self.tool("write_draft_file", arguments)
        self.tool_error("write_draft_file", arguments, "revision conflict")
        self.assertFalse((self.project.directory / "drafts/outside.txt").exists())

    def test_failed_validation_is_a_check_result_and_context_blocks_symlinks(self):
        draft = self.create_draft()
        draft = self.tool(
            "write_draft_file",
            {
                "project_id": self.project.id,
                "draft_id": draft["id"],
                "expected_revision": draft["revision"],
                "path": "broken.py",
                "content": "def broken(\n",
            },
        )
        result = self.tool(
            "validate_draft",
            {
                "project_id": self.project.id,
                "draft_id": draft["id"],
                "expected_revision": draft["revision"],
            },
        )
        self.assertEqual(result["status"], "failed")
        secret = self.root / "secret.txt"
        secret.write_text("do not expose")
        (code_directory(self.project) / "link.txt").symlink_to(secret)
        self.tool_error(
            "get_project_context", {"project_id": self.project.id}, "regular files"
        )


class MCPClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_ai_client_connects_to_console_http_transport(self):
        app = create_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:3000"
            ) as http,
            streamable_http_client("http://127.0.0.1:3000/mcp", http_client=http) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            self.assertEqual(initialized.serverInfo.name, "Reqlica")
            tools = await session.list_tools()
            self.assertIn("upsert_endpoint", {tool.name for tool in tools.tools})
            result = await session.call_tool(
                "get_project_context", {"project_id": "f" * 32}
            )
            self.assertTrue(result.isError)
