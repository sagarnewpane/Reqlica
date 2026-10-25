"""Exercise AI tool discovery and draft generation over the console's HTTP MCP."""

import json
import os
import sqlite3
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

    def test_package_discovery_compact_inventory_and_annotations(self):
        from reqlica.console import packages
        tools = {tool['name']: tool for tool in self.rpc('tools/list')['tools']}
        self.assertTrue(tools['install_package']['annotations']['destructiveHint'])
        self.assertTrue(tools['install_package']['annotations']['openWorldHint'])
        self.assertFalse(tools['preview_package_install']['annotations']['readOnlyHint'])
        self.assertIn('human', tools['install_package']['inputSchema']['properties']['confirm']['description'])
        info = {'installed':{'demo':'1'}, 'jobs':[{'id':'a'*32,'status':'review','requirement':'demo', 'log':'large output'}]}
        with patch.object(packages, 'package_info', return_value=info):
            result = self.tool('inspect_runtime_packages', {'project_id':self.project.id})
        self.assertEqual(result['installed_count'], 1)
        self.assertNotIn('installed', result)
        self.assertNotIn('log', result['jobs'][0])

    def test_package_tools_preserve_plan_pins_and_require_confirmation(self):
        from reqlica.console import packages
        ids = {'project_id':self.project.id}
        with patch.object(packages, 'preview_package', return_value={'id':'a'*32,'status':'resolving'}) as preview:
            self.tool('preview_package_install', {**ids,'package':'PyJWT','version':'2.10.1','expected_environment_revision':'b'*64})
            body = preview.call_args.args[1]
            self.assertEqual(body.package, 'PyJWT')
            self.assertEqual(body.expected_revision, 'b'*64)
        self.tool_error('install_package', {**ids,'job_id':'a'*32,'expected_plan_digest':'c'*64,'confirm':False}, 'confirmation')
        with patch.object(packages, 'install_package', return_value={'id':'a'*32,'status':'installing'}) as install:
            self.tool('install_package', {**ids,'job_id':'a'*32,'expected_plan_digest':'c'*64,'confirm':True})
            self.assertEqual(install.call_args.args[2].expected_plan_digest, 'c'*64)
            self.assertTrue(install.call_args.args[2].confirm)
        with patch.object(packages, 'install_package', side_effect=ValueError('Runtime packages changed')):
            self.tool_error('install_package', {**ids,'job_id':'a'*32,'expected_plan_digest':'c'*64,'confirm':True}, 'Runtime packages changed')
        with patch.object(packages, 'preview_package') as preview:
            response = self.rpc('tools/call', {'name':'preview_package_install','arguments':{**ids,'package':'-r evil','expected_environment_revision':'b'*64}})
            self.assertTrue(response['isError'])
            preview.assert_not_called()

    def test_package_job_polling_is_read_only_and_output_is_opt_in(self):
        from reqlica.console import packages
        args = {'project_id':self.project.id,'job_id':'a'*32}
        def result(*_args):
            return {'id':'a'*32,'status':'failed','message':'x'*3000,'log':'details'}
        with patch.object(packages, 'inspect_job', side_effect=result) as read, patch.object(packages, 'install_package') as install:
            compact = self.tool('get_package_install_job', args)
            expanded = self.tool('get_package_install_job', {**args,'include_output':True})
            self.assertNotIn('log', compact)
            self.assertEqual(len(compact['message']), 1024)
            self.assertEqual(expanded['log'], 'details')
            self.assertEqual(read.call_count, 2)
            install.assert_not_called()

    def test_saved_request_history_is_readable_without_draft_or_execution(self):
        from reqlica.projects.inspection import record_event
        record_event(self.project.directory, {"kind": "request", "request_id": "a" * 32, "status_code": 429})
        result = self.tool("read_project_logs", {"project_id": self.project.id, "request_id": "a" * 32})
        self.assertEqual(result["events"][0]["status_code"], 429)
        self.assertEqual(self.tool("list_drafts", {"project_id": self.project.id})["drafts"], [])

    def test_migration_documentation_survives_chat_compaction_and_examples_compile(self):
        from mcp.types import Tool
        from reqlica.generation import agent
        from reqlica.projects.migrations import Operation, compile_operations
        from reqlica.projects.schema_plan import SchemaPlan
        metadata = next(t for t in self.rpc("tools/list")["tools"] if t["name"] == "preview_database_migration")
        run = {"draft_id": None, "allow_execution": False, "tool_groups": ["database"], "messages": []}
        with patch.object(agent, "_has_current_context", return_value=True):
            exposed = agent._model_tools([Tool.model_validate(metadata)], run)[0]["function"]
        self.assertIn("No add_table", exposed["description"])
        self.assertIn("database/schema.json", exposed["description"])
        self.assertIn("EXISTING LIVE", exposed["description"])
        self.assertIn("INTEGER primary keys", exposed["description"])
        self.assertIn("existing source columns", exposed["description"])
        description = metadata["inputSchema"]["properties"]["operations_json"]["description"]
        self.assertEqual(exposed["parameters"]["properties"]["operations_json"]["description"], description)
        # Validate the literal examples users/models receive, including all six
        # operations and the separate additive new-table plan.
        decoder, examples, offset = json.JSONDecoder(), [], 0
        while (start := description.find("{", offset)) != -1:
            example, consumed = decoder.raw_decode(description[start:])
            examples.append(example)
            offset = start + consumed
        self.assertEqual(len(examples), 7)
        for example in examples[:-1]:
            self.assertTrue(compile_operations([Operation.model_validate(example)]))
        SchemaPlan.model_validate(examples[-1])

    def test_migration_preview_accepts_documented_existing_table_shape_without_live_changes(self):
        from reqlica.projects.database_setup import database_path
        path = database_path(get_project(self.project.id))
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        result = self.tool("preview_database_migration", {"project_id": self.project.id,
            "operations_json": json.dumps([{"op": "add_column", "table": "users", "column": {"name": "nickname", "type": "TEXT", "nullable": True}}])})
        self.assertEqual(result["plan"]["operations"][0]["op"], "add_column")
        with sqlite3.connect(path) as db:
            self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(users)")], ["id"])

    def test_chat_catalog_preserves_argument_guidance_for_every_enabled_tool_group(self):
        from copy import deepcopy
        from mcp.types import Tool
        from reqlica.generation import agent
        catalog = self.rpc("tools/list")["tools"]
        original = deepcopy(catalog)
        discovered = [Tool.model_validate(item) for item in catalog]
        by_name = {tool.name: tool for tool in discovered}
        seen = set()
        run = {"draft_id": "a" * 32, "allow_execution": True, "allow_edits": True,
               "repair_required": True, "messages": []}
        overrides = {("get_project_context", "include_source"), ("get_project_context", "include_database"),
                     ("write_draft_file", "path"), ("write_draft_file", "content")}
        with patch.object(agent, "_has_current_context", return_value=True):
            for group in agent.TOOL_GROUPS:
                tools = agent._model_tools(discovered, {**run, "tool_groups": [group]})
                for tool in tools:
                    function = tool["function"]
                    name = function["name"]
                    seen.add(name)
                    if name not in by_name:
                        continue  # Local discover_tools/finish_task have inline schemas.
                    properties = function["parameters"]["properties"]
                    for parameter, field in properties.items():
                        self.assertTrue(field.get("description"), (name, parameter))
                        if (name, parameter) not in overrides:
                            self.assertEqual(field["description"], by_name[name].inputSchema["properties"][parameter]["description"], (name, parameter))
        self.assertEqual(seen, agent._allowed(run))
        self.assertEqual(catalog, original)
        # All external tools also retain clear prerequisite/result guidance in
        # their first paragraph, which chat uses for tools without a local summary.
        for tool in discovered:
            self.assertTrue(tool.description.split("\n\n", 1)[0].strip(), tool.name)
        endpoint = agent._tool_schema(by_name["upsert_endpoint"])["$defs"]["Endpoint"]["properties"]
        self.assertIn("No query", endpoint["path"]["description"])
        self.assertIn("Together with path", endpoint["method"]["description"])
        self.assertIn("concrete request path", agent._tool_schema(by_name["call_draft_endpoint"])["properties"]["path"]["description"].lower())

    def test_serialized_data_and_policy_documentation_examples_validate(self):
        from reqlica.generation.data_schemas import DataValidationRequest, RelatedDataValidationRequest
        from reqlica.projects.related_data import validate_related_records
        from reqlica.projects.scenarios import ScenarioSettings
        from reqlica.projects.cors import CorsSettings
        tools = {tool["name"]: tool for tool in self.rpc("tools/list")["tools"]}

        def examples(tool, parameter):
            description = tools[tool]["inputSchema"]["properties"][parameter]["description"]
            decoder, result, offset = json.JSONDecoder(), [], 0
            while offset < len(description):
                candidates = [i for char in ("{", "[") if (i := description.find(char, offset)) != -1]
                if not candidates:
                    break
                start = min(candidates)
                example, consumed = decoder.raw_decode(description[start:])
                result.append(example)
                offset = start + consumed
            return result

        records, related = examples("preview_mock_data", "records_json")
        DataValidationRequest(records=records)
        RelatedDataValidationRequest(records_by_table=related, expected_schema="a" * 64)
        validate_related_records(related)
        scenarios, clear, cors = examples("set_traffic_policy", "settings_json")
        ScenarioSettings.model_validate(scenarios)
        ScenarioSettings.model_validate(clear)
        CorsSettings.model_validate(cors)
        from reqlica.projects.schema_plan import SchemaPlan
        SchemaPlan.model_validate(examples("write_draft_file", "content")[0])

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
                "read_project_logs",
                "preview_database_migration",
                "inspect_runtime_packages",
                "preview_package_install",
                "get_package_install_job",
                "install_package",
                "get_traffic_policies",
                "set_traffic_policy",
                "reset_scenario_counters",
                "get_worker_status",
                "control_project_worker",
                "get_data_baseline",
                "capture_data_baseline",
                "reset_project_data",
                "list_database_migrations",
                "inspect_project_archive",
                "export_project_archive",
                "import_project_archive",
                "relocate_project",
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

    def test_mock_data_refuses_hash_columns_without_writing_live_data(self):
        path = self.project.directory / "data.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL)")
        original = path.read_bytes()
        context = self.tool("get_mock_data_context", {"project_id": self.project.id})
        self.assertIn("users", context["application"]["unsupported_tables"])
        self.tool_error("preview_mock_data", {"project_id": self.project.id,
            "target": "sqlite", "table": "users", "expected_schema": context["schema"]["fingerprint"],
            "records_json": '[{"password_hash":"invented-hash"}]'}, "application-specific hashing")
        self.assertEqual(path.read_bytes(), original)

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
