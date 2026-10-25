"""Real MCP client/server integration with deterministic model tool calls."""

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation import agent
from reqlica.generation.errors import GenerationError
from reqlica.mcp.server import create_server
from reqlica.projects import drafts
from reqlica.projects.store import create_project, get_project, registry_directory
from reqlica.projects.versions import code_directory, content_digest, atomic_write_json, snapshot_code
from ai_fixtures import HANDLER, TEST_SOURCE, ProviderFixture


def tool_message(name, arguments):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": uuid4().hex, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments),
        },
    }]}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {"REQLICA_HOME": str(self.directory / "home")}))
        self.project = create_project(self.directory / "books", "Books")
        self.client = self.enterContext(TestClient(create_app()))
        self.complete = self.enterContext(patch("reqlica.generation.agent.provider.complete"))
        self.run_id = uuid4().hex
        self.root = f"/api/projects/{self.project.id}/agent-runs"
        self.headers = {"X-Reqlica-Groq-Key": "request-only-key"}

    def create(self, **settings):
        response = self.client.put(f"{self.root}/{self.run_id}", json={"message": "Add reader greetings", "model": "test-tool-model", **settings})
        self.assertEqual(response.status_code, 200, response.text)
        self.run = response.json()
        return self.run

    def step(self):
        response = self.client.post(f"{self.root}/{self.run_id}/step", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.run = response.json()
        return self.run

    def tool(self, name, **arguments):
        self.complete.return_value = (tool_message(name, {"project_id": self.project.id, **arguments}), {"total_tokens": 10})
        result = self.step()
        self.assertEqual(result["status"], "ready", result)
        return self.step()

    def test_ai_created_draft_persists_short_title(self):
        self.create(message="Create a bookstore demo with books, stock, and reader greetings")
        self.complete.return_value = (tool_message("create_draft", {"name": "Bookstore API demo"}), None)
        self.step()
        self.step()
        saved = drafts.inspect_draft(self.project.id, self.run["draft_id"])
        self.assertEqual(saved["name"], "Bookstore API demo")
        listed = drafts.list_drafts(self.project.id)
        self.assertEqual(listed["drafts"][0]["name"], "Bookstore API demo")

    def test_chat_uses_saved_project_key_without_browser_key_or_checkpoint_leak(self):
        self.headers = {}
        response = self.client.put(f"/api/projects/{self.project.id}/ai/key", json={
            "provider": "groq", "api_key": "persistent-project-key"})
        self.assertEqual(response.status_code, 200, response.text)
        self.create()
        self.complete.return_value = ({"role": "assistant", "content": "Ready"}, None)
        self.assertEqual(self.step()["status"], "completed")
        self.assertEqual(self.complete.call_args.args[2].get_secret_value(), "persistent-project-key")
        self.assertNotIn("persistent-project-key", agent._path(self.project.id, self.run_id).read_text())
        self.assertNotIn("persistent-project-key", self.client.get(f"{self.root}/{self.run_id}/context").text)

    def test_public_progress_identifies_next_model_or_tool_without_exposing_arguments(self):
        self.create()
        self.assertEqual(self.run["next_action"], {"kind": "model"})
        self.complete.return_value = (tool_message("get_project_context", {"include_source": False}), None)
        self.step()
        self.assertEqual(self.run["next_action"], {"kind": "tool", "name": "get_project_context"})
        self.step()
        self.assertEqual(self.run["next_action"], {"kind": "model"})
        self.complete.return_value = ({"role": "assistant", "content": "Ready"}, None)
        self.step()
        self.assertIsNone(self.run["next_action"])

    def test_unsupported_reasoning_is_rejected_before_creating_a_run_or_spending_budget(self):
        with patch("reqlica.generation.reasoning.reasoning_options", return_value={"modes": ["default"]}):
            response = self.client.put(f"{self.root}/{self.run_id}", json={
                "message": "Explain", "reasoning_mode": "low", "provider": "compatible",
                "base_url": "https://openrouter.ai/api/v1", "model": "test-model"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_reasoning_mode")
        self.assertEqual(response.json()["detail"]["reasoning_mode"], "low")
        self.assertEqual(self.client.get(self.root).json()["runs"], [])
        self.complete.assert_not_called()

    def test_ollama_conversation_can_send_and_reply_without_a_key(self):
        self.headers = {}
        self.create(provider="ollama", base_url="http://localhost:11434/v1", model="installed:model")
        self.complete.return_value = ({"role": "assistant", "content": "Ready"}, None)
        self.assertEqual(self.step()["status"], "completed")

        request = self.complete.call_args.args[1]
        self.assertEqual(request.provider, "ollama")
        self.assertEqual(request.base_url, "http://localhost:11434")
        self.assertIsNone(self.complete.call_args.args[2])
        response = self.client.post(f"{self.root}/{self.run_id}/replies", json={
            "message": "Explain more", "reply_id": uuid4().hex,
            "expected_turn": self.run["turn"], "expected_revision": self.run["revision"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.step()["status"], "completed")

    def test_context_inspection_captures_exact_input_without_keys_or_another_model_call(self):
        self.create()
        self.complete.return_value = ({"role": "assistant", "content": "Reply not in the input"}, {"prompt_tokens": 120, "completion_tokens": 15})
        self.step()
        sent_messages = self.complete.call_args.args[0]
        sent_tools = self.complete.call_args.kwargs["tools"]
        calls = self.complete.call_count
        response = self.client.get(f"{self.root}/{self.run_id}/context")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertEqual(data["source"], "captured")
        self.assertEqual(data["messages"], sent_messages)
        self.assertEqual(data["tools"], sent_tools)
        self.assertEqual(data["summary"]["bytes"], len(json.dumps({"messages": sent_messages, "tools": sent_tools}, ensure_ascii=False, separators=(",", ":")).encode()))
        self.assertEqual(data["last_model_response"]["usage"]["prompt_tokens"], 120)
        self.assertNotIn("request-only-key", response.text)
        self.assertNotIn("model_context", self.run)
        self.assertNotIn("messages", self.run["context"])
        self.assertEqual(self.complete.call_count, calls)

    def test_older_context_reconstruction_is_read_only_and_project_scoped(self):
        self.create()
        path = agent._path(self.project.id, self.run_id)
        before = path.read_bytes()
        response = self.client.get(f"{self.root}/{self.run_id}/context")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "reconstructed")
        self.assertEqual(path.read_bytes(), before)
        self.complete.assert_not_called()
        other = create_project(self.directory / "other-context", "Other")
        self.assertEqual(self.client.get(f"/api/projects/{other.id}/agent-runs/{self.run_id}/context").status_code, 404)

    def test_failed_response_keeps_actual_token_usage_and_input_snapshot(self):
        self.create(max_tokens=65536)
        self.complete.side_effect = GenerationError("Stopped", code="output_token_limit", diagnostics={
            "finish_reason": "length", "max_output_tokens": 65536,
            "usage": {"prompt_tokens": 3650, "completion_tokens": 446, "total_tokens": 4096},
        })
        result = self.step()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["last_model_response"]["usage"]["completion_tokens"], 446)
        self.assertEqual(result["usage"][-1]["prompt_tokens"], 3650)
        snapshot = self.client.get(f"{self.root}/{self.run_id}/context").json()
        self.assertEqual(snapshot["source"], "captured")
        self.assertEqual(snapshot["summary"]["max_output_tokens"], 65536)

    def reasoning_failure(self, **updates):
        self.create()
        path = agent._path(self.project.id, self.run_id)
        saved = agent._read(path)
        saved["request"]["reasoning_mode"] = "low"
        saved.update(status="failed", model_calls=1, error={"message": agent.UNSUPPORTED_MODE_MESSAGE}, **updates)
        agent._save(path, saved)
        return path, saved, {"expected_revision": saved["revision"], "expected_reasoning_mode": "low"}

    def test_manual_openrouter_id_can_create_a_conversation_without_catalog_approval(self):
        with patch("reqlica.generation.provider.openrouter_model", side_effect=AssertionError("Discovery cannot gate creation")):
            self.create(provider="compatible", model="unlisted/model:free", base_url="https://openrouter.ai/api/v1")
        self.assertEqual(self.run["model_calls"], 0)
        self.assertEqual(self.run["provider"]["model"], "unlisted/model:free")
        self.complete.assert_not_called()

    def test_compact_tool_catalog_preserves_business_parameters_and_full_conversation(self):
        self.create(allow_execution=True)
        before = agent._read(agent._path(self.project.id, self.run_id))
        discovered = asyncio.run(create_server().list_tools())
        original_catalog = [tool.model_dump(mode="json") for tool in discovered]
        canonical = {tool.name: tool for tool in discovered if tool.name in agent._allowed(before)}
        self.complete.return_value = ({"role": "assistant", "content": "Ready to inspect the API"}, None)
        self.step()
        messages = self.complete.call_args.args[0]
        sent = self.complete.call_args.kwargs["tools"]
        self.assertEqual(messages[1:], before["messages"])
        self.assertTrue(messages[0]["content"].startswith(agent.INSTRUCTIONS))
        self.assertEqual({tool["function"]["name"] for tool in sent}, set(canonical))
        for tool in sent:
            original = canonical[tool["function"]["name"]].inputSchema
            schema = tool["function"]["parameters"]
            self.assertEqual(set(schema["properties"]), set(original["properties"]) - {"project_id", "draft_id", "expected_revision"})
            self.assertEqual(set(schema.get("required", [])), set(original.get("required", [])) - {"project_id", "draft_id", "expected_revision"})
            for name, field in schema["properties"].items():
                expected = original["properties"][name]
                for key in expected.keys() - {"title", "description"}:
                    self.assertEqual(field[key], expected[key], (tool["function"]["name"], name, key))
                if "description" in expected:
                    self.assertEqual(field["description"], expected["description"])
            if tool["function"]["name"] == "write_draft_file":
                self.assertIn("Reqlica supplies the current revision", tool["function"]["description"])
        verbose = [{"type": "function", "function": {"name": tool.name, "description": tool.description,
                    "parameters": tool.inputSchema}} for tool in canonical.values()]
        self.assertLess(len(json.dumps(sent)), len(json.dumps(verbose)) * 0.8)
        self.assertEqual([tool.model_dump(mode="json") for tool in discovered], original_catalog)
        self.assertEqual(agent._read(agent._path(self.project.id, self.run_id))["messages"][:-1], before["messages"])
        # The optimization preserves permission filtering for read-only conversations.
        read_only = {**before, "allow_edits": False, "allow_execution": False}
        tools = agent._model_tools(discovered, read_only)
        self.assertEqual({tool["function"]["name"] for tool in tools}, agent.READ)

    def test_model_calls_without_scope_ids_still_use_the_assigned_project_and_draft(self):
        self.create()
        calls = [
            ("create_draft", {}),
            ("write_draft_file", {"path": "endpoints/greetings.py", "content": HANDLER}),
            ("upsert_endpoint", {"endpoint": {"method": "GET", "path": "/greet", "handler": "endpoints/greetings.py:greet"}}),
        ]
        for name, arguments in calls:
            if name != "create_draft":
                arguments["expected_revision"] = self.run["revision"]
            self.complete.return_value = (tool_message(name, arguments), None)
            self.step()
            self.assertEqual(self.step()["status"], "ready", self.run)
        result = drafts.inspect_draft(self.project.id, self.run["draft_id"])
        self.assertIn("endpoints/greetings.py", result["files"])
        manifest = json.loads((drafts.draft_directory(self.project, self.run["draft_id"]) / "manifest.json").read_text())
        self.assertTrue(any(endpoint["path"] == "/greet" for endpoint in manifest["endpoints"]))

    def test_saved_unlisted_model_can_resume_without_losing_history_or_changing_provider(self):
        self.create()
        self.tool("create_draft")
        path = agent._path(self.project.id, self.run_id)
        saved = agent._read(path)
        saved["request"].update(provider="compatible", base_url="https://openrouter.ai/api/v1", model="unlisted/model:free")
        saved.update(status="failed", error={"code": "model_unavailable", "message": "Old catalog check"})
        agent._save(path, saved)
        self.complete.return_value = ({"role": "assistant", "content": "Available"}, None)
        with patch("reqlica.generation.provider.openrouter_model", side_effect=AssertionError("Discovery cannot gate resume")):
            result = self.step()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_calls"], saved["model_calls"] + 1)
        after = agent._read(path)
        for key in ("tool_calls", "events", "draft_id", "revision", "request"):
            self.assertEqual(after[key], saved[key], key)
        self.assertEqual(after["messages"][:-1], saved["messages"])
        self.assertEqual(self.complete.call_args.args[1].model, "unlisted/model:free")

    def test_saved_reasoning_can_reset_to_default_without_replaying_work_or_changing_budget(self):
        path, before, body = self.reasoning_failure()
        inspected = self.client.get(f"{self.root}/{self.run_id}").json()
        self.assertTrue(inspected["can_reset_reasoning"])
        self.assertEqual(inspected["provider"]["reasoning_mode"], "low")
        self.assertEqual(self.step(), inspected)
        response = self.client.post(f"{self.root}/{self.run_id}/reasoning-default", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["can_reset_reasoning"])
        self.assertEqual(result["provider"]["reasoning_mode"], "default")
        after = agent._read(path)
        for key in ["id", "draft_id", "revision", "messages", "events", "checks", "usage", "model_calls", "tool_calls", "allow_execution", "allow_edits"]:
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.client.post(f"{self.root}/{self.run_id}/reasoning-default", json=body).json(), result)
        self.complete.assert_not_called()

    def test_reasoning_reset_rejects_stale_scope_pending_tools_other_failures_and_exhausted_budget(self):
        path, saved, body = self.reasoning_failure()
        url = f"{self.root}/{self.run_id}/reasoning-default"
        for changed in [{"expected_revision": "f" * 64}, {"expected_reasoning_mode": "high"}]:
            self.assertEqual(self.client.post(url, json={**body, **changed}).status_code, 409)
        self.assertEqual(self.client.post(url, json={**body, "allow_execution": True}).status_code, 422)
        for changes in [{"pending": [tool_message("create_draft", {})]}, {"status": "running"},
                        {"error": {"message": "Some unrelated error"}}, {"model_calls": agent.MAX_MODEL_CALLS}]:
            variant = {**saved, **changes}
            agent._save(path, variant)
            self.assertEqual(self.client.post(url, json=body).status_code, 409, changes)
        agent._save(path, saved)
        (code_directory(self.project) / "endpoints/hello.py").write_text("def hello(): return {}\n")
        self.assertEqual(self.client.post(url, json=body).status_code, 409)
        self.assertEqual(agent._read(path)["request"]["reasoning_mode"], "low")
        self.complete.assert_not_called()

    def test_retired_routes_cannot_run_or_mutate_saved_drafts(self):
        saved = drafts.create_draft(self.project.id)
        before = content_digest(code_directory(self.project))
        legacy = self.project.directory / ".reqlica" / "generation-jobs"
        legacy.mkdir(exist_ok=True)
        checkpoint = legacy / (uuid4().hex + ".json")
        checkpoint.write_text('{"status":"ready","stage":"files"}')
        original = checkpoint.read_bytes()
        root = f"/api/projects/{self.project.id}"
        routes = [("POST", "/generate"), ("POST", "/clarify"), ("POST", "/ask"),
            ("POST", "/edit-endpoint"), ("GET", "/endpoint-source"),
            ("GET", "/generation-jobs"), ("PUT", "/generation-jobs/" + self.run_id),
            ("POST", "/generation-jobs/" + self.run_id + "/step"),
            ("POST", "/generation-jobs/" + self.run_id + "/resume"),
            ("POST", "/drafts/" + saved["id"] + "/repair"),
            ("POST", "/data/generate"), ("POST", "/data/related/generate")]
        for method, path in routes:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, root + path, json={"requirements": "ignored"})
                self.assertEqual(response.status_code, 404, response.text)
        self.complete.assert_not_called()
        self.assertEqual(drafts.inspect_draft(self.project.id, saved["id"])["revision"], saved["revision"])
        self.assertEqual(content_digest(code_directory(self.project)), before)
        self.assertEqual(checkpoint.read_bytes(), original)

    def test_read_only_conversation_answers_and_rejects_fabricated_edits(self):
        before = content_digest(code_directory(self.project))
        self.create(allow_edits=False)
        self.tool("get_project_context", include_source=False)
        exposed = {item["function"]["name"] for item in self.complete.call_args.kwargs["tools"]}
        self.assertFalse(exposed & (agent.EDIT | agent.EXECUTE | {"create_draft"}))
        self.assertIn("preview_mock_data", exposed)
        self.tool("create_draft")
        self.assertEqual(self.run["events"][-1]["status"], "error")
        self.complete.return_value = ({"role": "assistant", "content": "This API exposes GET /hello."}, None)
        self.assertEqual(self.step()["status"], "completed")
        self.assertTrue(self.run["can_reply"])
        self.assertFalse(self.run["allow_edits"])
        self.assertIsNone(self.run["draft_id"])
        self.assertEqual(content_digest(code_directory(self.project)), before)
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_legacy_project_can_answer_but_cannot_enable_draft_edits(self):
        directory = self.directory / "legacy"
        (directory / "endpoints").mkdir(parents=True)
        legacy_id = uuid4().hex
        atomic_write_json(directory / "project.json", {"id": legacy_id, "name": "Legacy"})
        atomic_write_json(directory / "manifest.json", {"endpoints": []})
        atomic_write_json(registry_directory() / (legacy_id + ".json"), {"directory": str(directory)})
        self.project = get_project(legacy_id)
        self.root = f"/api/projects/{legacy_id}/agent-runs"
        rejected = self.client.put(f"{self.root}/{uuid4().hex}", json={"model": "test-tool-model", "message": "Explain this"})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.create(allow_edits=False)
        self.tool("get_project_context", include_source=False, include_database=False)
        self.assertEqual(self.run["events"][-1]["status"], "done", self.run)
        self.complete.return_value = ({"role": "assistant", "content": "This legacy project has no endpoints."}, None)
        self.assertEqual(self.step()["status"], "completed")
        self.assertIsNone(self.run["draft_id"])
        self.assertIsNone(get_project(legacy_id).active_version)

    def test_data_previews_use_real_mcp_without_draft_or_live_insertion(self):
        path = self.project.directory / "data.sqlite"
        with sqlite3.connect(path) as db:
            db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                INSERT INTO users VALUES(100, 'private-existing-name');
                CREATE TABLE posts(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), title TEXT NOT NULL);""")
        database = path.read_bytes()
        self.create(allow_edits=False)
        self.tool("get_mock_data_context")
        checkpoint = agent._read(agent._path(self.project.id, self.run_id))
        context = json.loads(checkpoint["messages"][-1]["content"])
        self.assertNotIn("private-existing-name", json.dumps(context))
        schema = context["schema"]["fingerprint"]
        for settings in [
            {"target": "json", "records_json": '[{"label":"Sample"}]'},
            {"target": "sqlite", "table": "users", "expected_schema": schema,
             "records_json": '[{"name":"Ava"}]'},
            {"target": "related", "expected_schema": schema, "records_json": json.dumps({
                "users": [{"name": "Milo"}], "posts": [{"user_id": {"$ref": "users.0.id"}, "title": "Hello"}],
            })},
        ]:
            with self.subTest(target=settings["target"]):
                self.tool("preview_mock_data", **settings)
                preview = self.run["data_preview"]
                self.assertEqual(preview["validation"]["status"], "passed", self.run)
                self.assertEqual(preview["target"], settings["target"])
                self.assertEqual(path.read_bytes(), database)
        self.tool("preview_mock_data", target="sqlite", table="users", expected_schema=schema,
                  records_json='[{"name":"private-existing-name"}]')
        self.assertEqual(self.run["events"][-1]["status"], "failed")
        self.assertEqual(self.run["data_preview"]["validation"]["status"], "failed")
        self.assertEqual(path.read_bytes(), database)
        self.assertIsNone(self.run["draft_id"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])
        self.tool("insert_data", records=[{"name": "unauthorized"}])
        self.assertEqual(self.run["events"][-1]["status"], "error")
        self.assertEqual(path.read_bytes(), database)

    def test_full_build_reads_edits_checks_and_never_publishes(self):
        before = content_digest(code_directory(self.project))
        database = (self.project.directory / "data.sqlite").read_bytes()
        self.create(allow_execution=True)
        self.assertEqual(self.client.get(self.root).json()["runs"][0]["id"], self.run_id)
        self.tool("get_project_context", include_source=False)
        self.tool("create_draft")
        working_id = self.run["draft_id"]
        for path, content in [("endpoints/greetings.py", HANDLER), ("tests/test_greetings.py", TEST_SOURCE)]:
            self.tool("write_draft_file", draft_id=working_id, expected_revision=self.run["revision"], path=path, content=content)
        self.tool("upsert_endpoint", draft_id=working_id, expected_revision=self.run["revision"], endpoint={"method": "GET", "path": "/greet", "handler": "endpoints/greetings.py:greet"})
        self.tool("validate_draft", draft_id=working_id, expected_revision=self.run["revision"])
        self.tool("test_draft", draft_id=working_id, expected_revision=self.run["revision"])
        self.assertEqual(self.run["checks"]["validate_draft"]["status"], "passed", self.run)
        self.assertEqual(self.run["checks"]["test_draft"]["status"], "passed", self.run)
        self.complete.return_value = ({"role": "assistant", "content": "Greeting added. Validation and tests pass. Review the draft."}, None)
        result = self.step()
        self.assertEqual(result["status"], "completed")
        calls = self.complete.call_count
        self.assertEqual(self.step(), result)
        self.assertEqual(calls, self.complete.call_count)
        self.assertEqual(content_digest(code_directory(self.project)), before)
        self.assertEqual(get_project(self.project.id).active_version, self.project.active_version)
        self.assertEqual((self.project.directory / "data.sqlite").read_bytes(), database)
        self.assertIsNone(drafts.inspect_draft(self.project.id, working_id)["approval"])
        checkpoint = agent._read(agent._path(self.project.id, self.run_id))
        self.assertNotIn("request-only-key", json.dumps(checkpoint))
        self.assertEqual(self.complete.call_args.args[2].get_secret_value(), "request-only-key")
        self.assertTrue(any(m["role"] == "tool" for m in self.complete.call_args.args[0]))
        self.assertTrue(any(event.get("path") == "endpoints/greetings.py" for event in result["events"]))

    def test_scope_execution_and_paths_are_enforced_even_for_fabricated_calls(self):
        other = create_project(self.directory / "other", "Other")
        unrelated = drafts.create_draft(self.project.id)
        chosen = drafts.create_draft(self.project.id)
        self.create(draft_id=chosen["id"], expected_revision=chosen["revision"])
        for name, arguments in [
            ("get_project_context", {"project_id": other.id}),
            ("get_project_context", {"draft_id": unrelated["id"]}),
            ("create_draft", {}), ("activate_draft", {}), ("approve_draft", {}),
            ("test_draft", {"expected_revision": chosen["revision"]}),
            ("write_draft_file", {"expected_revision": chosen["revision"], "path": ".env", "content": "KEY=bad"}),
        ]:
            self.complete.return_value = (tool_message(name, {"project_id": self.project.id, **arguments}), None)
            self.step()
            self.step()
            self.assertEqual(self.run["events"][-1]["status"], "error", name)
        tools = {item["function"]["name"] for item in self.complete.call_args.kwargs["tools"]}
        self.assertFalse(tools & agent.EXECUTE)
        self.assertNotIn("create_draft", tools)
        self.assertNotIn("list_drafts", tools)
        self.assertEqual(len(drafts.list_drafts(self.project.id)["drafts"]), 2)
        self.assertEqual(drafts.inspect_draft(self.project.id, chosen["id"])["revision"], chosen["revision"])
        self.assertEqual(drafts.list_drafts(other.id)["drafts"], [])

    def test_server_binds_revision_across_edits_even_when_model_sends_stale_hash(self):
        self.create()
        self.tool("create_draft")
        old_revision = self.run["revision"]
        self.tool("write_draft_file", path="endpoints/greetings.py", content=HANDLER,
                  expected_revision=old_revision)
        current_revision = self.run["revision"]
        self.assertNotEqual(old_revision, current_revision)

        self.complete.return_value = (tool_message("write_draft_file", {
            "path": "endpoints/extra.py", "content": "x = 1\n",
            "expected_revision": old_revision}), None)
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.step()["status"], "ready")
        checkpoint = agent._read(agent._path(self.project.id, self.run_id))
        feedback = json.loads(checkpoint["messages"][-1]["content"])
        self.assertEqual(feedback["revision"], self.run["revision"])
        self.assertNotEqual(self.run["revision"], current_revision)

        self.complete.return_value = (tool_message("upsert_endpoint", {
            "endpoint": {"method": "GET", "path": "/greet", "handler": "endpoints/greetings.py:greet"},
            "expected_revision": old_revision}), None)
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.step()["status"], "ready")
        self.assertNotIn("tool_argument_rejections", agent._read(agent._path(self.project.id, self.run_id)))
        manifest = json.loads((drafts.draft_directory(self.project, self.run["draft_id"]) / "manifest.json").read_text())
        self.assertTrue(any(endpoint["path"] == "/greet" for endpoint in manifest["endpoints"]))

    def test_batched_file_writes_use_successive_server_revisions(self):
        self.create()
        self.tool("create_draft")
        first = tool_message("write_draft_file", {"path": "endpoints/greetings.py", "content": HANDLER})
        second = tool_message("write_draft_file", {"path": "tests/test_greetings.py", "content": TEST_SOURCE})
        first["tool_calls"].extend(second["tool_calls"])
        self.complete.return_value = (first, None)
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.step()["status"], "ready")
        saved = drafts.inspect_draft(self.project.id, self.run["draft_id"])
        self.assertIn("endpoints/greetings.py", saved["files"])
        self.assertIn("tests/test_greetings.py", saved["files"])
        self.assertEqual(self.run["events"][-1]["status"], "done")

    def test_json_string_endpoint_error_then_correction_does_not_interrupt_run(self):
        self.create()
        self.tool("create_draft")
        self.tool("write_draft_file", path="endpoints/greetings.py", content=HANDLER)
        bad = json.dumps({"method": "GET", "path": "/greet", "handler": "endpoints/greetings:greet"})
        self.tool("upsert_endpoint", endpoint=bad)
        self.assertEqual(self.run["events"][-1]["status"], "error")
        self.assertIn(".py file and function", self.run["events"][-1]["detail"])
        good = json.dumps({"method": "GET", "path": "/greet", "handler": "endpoints/greetings.py:greet"})
        self.tool("upsert_endpoint", endpoint=good)
        self.assertEqual(self.run["status"], "ready")
        self.assertEqual(self.run["events"][-1]["status"], "done")
        self.assertEqual(self.run["events"][-1]["route"], "GET /greet")
        manifest = json.loads((drafts.draft_directory(self.project, self.run["draft_id"]) / "manifest.json").read_text())
        self.assertTrue(any(endpoint["path"] == "/greet" for endpoint in manifest["endpoints"]))

    def test_unavailable_tool_returns_specific_safe_guidance(self):
        self.create(allow_execution=True)
        self.tool("create_draft")
        self.complete.return_value = (tool_message("list_drafts", {}), None)
        self.step()
        self.assertEqual(self.step()["status"], "ready")
        feedback = json.loads(agent._read(agent._path(self.project.id, self.run_id))["messages"][-1]["content"])
        self.assertEqual(feedback["code"], "tool_unavailable")
        self.assertIn("get_draft_changes", feedback["error"])

    def test_conflicting_edit_stops_before_model_or_tool_execution(self):
        selected = drafts.create_draft(self.project.id)
        self.create(draft_id=selected["id"], expected_revision=selected["revision"])
        drafts.write_draft_file(self.project.id, selected["id"], "endpoints/new.py", "x=1\n", selected["revision"])
        self.assertEqual(self.step()["status"], "conflict")
        self.complete.assert_not_called()
        self.assertFalse(self.run["can_resume"])

    def publish_snapshot(self, source):
        """Reproduce activation's new version ID and unchanged draft base."""
        version = uuid4().hex
        snapshot_code(source, self.project.directory / "versions" / version)
        metadata = self.project.model_copy(update={"active_version": version})
        atomic_write_json(self.project.directory / "project.json",
                          metadata.model_dump(mode="json", exclude={"directory"}))
        return version

    def test_selected_published_draft_starts_fresh_and_preserves_original(self):
        selected = drafts.create_draft(self.project.id)
        selected = drafts.write_draft_file(self.project.id, selected["id"],
            "endpoints/greetings.py", HANDLER, selected["revision"])
        args = self.project.id, selected["id"], selected["revision"]
        drafts.validate_draft(*args)
        drafts.test_draft(*args)
        drafts.approve_draft(*args)
        before = drafts.inspect_draft(self.project.id, selected["id"])
        published = self.publish_snapshot(drafts.draft_directory(self.project, selected["id"]))
        result = self.create(draft_id=selected["id"], expected_revision=selected["revision"])
        self.assertIsNone(result["draft_id"])
        self.assertEqual(result["source_draft_id"], selected["id"])
        self.assertIn("new draft", result["notice"])
        self.tool("create_draft")
        self.assertNotEqual(self.run["draft_id"], selected["id"])
        working = drafts.inspect_draft(self.project.id, self.run["draft_id"])
        self.assertEqual(working["base_version"], published)
        self.assertEqual(working["revision"], selected["revision"])
        self.assertEqual(drafts.inspect_draft(self.project.id, selected["id"]), before)

    def test_fresh_draft_after_publication_uses_current_base(self):
        self.publish_snapshot(code_directory(self.project))
        selected = drafts.create_draft(self.project.id)
        result = self.create(draft_id=selected["id"], expected_revision=selected["revision"])
        self.assertEqual(result["draft_id"], selected["id"])
        self.assertIsNone(result["notice"])
        self.tool("get_project_context", include_source=False)
        self.assertEqual(self.run["status"], "ready")

    def test_genuinely_diverged_old_draft_still_requires_review(self):
        selected = drafts.create_draft(self.project.id)
        drafts.write_draft_file(self.project.id, selected["id"],
            "endpoints/greetings.py", HANDLER, selected["revision"])
        selected = drafts.inspect_draft(self.project.id, selected["id"])
        self.publish_snapshot(code_directory(self.project))
        response = self.client.put(f"{self.root}/{self.run_id}", json={
            "message": "Continue", "model": "test-tool-model", "draft_id": selected["id"],
            "expected_revision": selected["revision"],
        })
        self.assertEqual(response.status_code, 409)
        self.assertIn("Start a new draft", response.json()["detail"])
        self.assertFalse(agent._path(self.project.id, self.run_id).exists())

    def test_failed_provider_is_resumable_but_interrupted_tool_is_not_replayed(self):
        self.create()
        self.complete.side_effect = GenerationError("Provider unavailable", 503)
        self.assertEqual(self.step()["status"], "failed")
        self.assertEqual(self.run["error"]["message"], "Provider unavailable")
        self.complete.side_effect = None
        self.complete.return_value = (tool_message("create_draft", {"project_id": self.project.id}), None)
        self.step()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["status"] = "running"
        agent._save(path, checkpoint)
        self.assertEqual(self.step()["status"], "interrupted")
        self.assertFalse(self.run["can_resume"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_creation_idempotence_and_budget_survive_reload(self):
        created = self.create()
        self.assertEqual(self.create(), created)
        self.assertEqual(self.client.put(f"{self.root}/{self.run_id}", json={"model": "test-tool-model", "message": "Different"}).status_code, 409)
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["model_calls"] = agent.MAX_MODEL_CALLS
        agent._save(path, checkpoint)
        self.assertEqual(self.step()["status"], "limit")
        self.complete.assert_not_called()

    def test_reported_token_budget_stops_pending_tools_and_survives_reload(self):
        self.create()
        self.assertEqual(agent.MAX_REPORTED_TOKENS, 100000)
        self.complete.return_value = (
            tool_message("create_draft", {"name": "Over budget draft"}),
            {"prompt_tokens": agent.MAX_REPORTED_TOKENS - 100, "completion_tokens": 100,
             "total_tokens": agent.MAX_REPORTED_TOKENS},
        )
        result = self.step()
        self.assertEqual(result["status"], "limit")
        self.assertEqual(result["reported_tokens"], agent.MAX_REPORTED_TOKENS)
        self.assertEqual(result["limits"]["total_tokens"], agent.MAX_REPORTED_TOKENS)
        self.assertFalse(result["can_resume"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])
        self.assertEqual(self.step()["status"], "limit")
        self.assertEqual(self.complete.call_count, 1)

    def test_one_request_cannot_run_model_steps_indefinitely(self):
        self.create()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["model_calls"] = agent.MAX_MODEL_CALLS_PER_TURN
        agent._save(path, checkpoint)
        self.assertEqual(self.step()["status"], "limit")
        self.assertIn("16 model calls", self.run["error"]["message"])
        self.complete.assert_not_called()

    def test_remaining_reported_budget_caps_the_next_output(self):
        self.create()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["usage"] = [{"total_tokens": agent.MAX_REPORTED_TOKENS - 1000}]
        agent._save(path, checkpoint)
        self.complete.return_value = ({"role": "assistant", "content": "Done"}, {"total_tokens": 100})
        self.assertEqual(self.step()["status"], "completed")
        self.assertEqual(self.complete.call_args.args[1].max_tokens, 1000)

    def test_explicit_recovery_retries_only_an_interrupted_model_step(self):
        self.create()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["status"] = "running"
        checkpoint["model_calls"] = 1
        agent._save(path, checkpoint)
        with agent._lease(path, "AI build"):
            active = self.client.post(f"{self.root}/{self.run_id}/recover")
        self.assertEqual(active.status_code, 409)
        self.assertEqual(agent._read(path)["status"], "running")
        response = self.client.post(f"{self.root}/{self.run_id}/recover")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ready")
        self.complete.assert_not_called()
        self.complete.return_value = ({"role": "assistant", "content": "Recovered"}, {"total_tokens": 25})
        self.assertEqual(self.step()["status"], "completed")
        self.assertEqual(self.run["model_calls"], 2)

        checkpoint = agent._read(path)
        checkpoint["status"] = "running"
        checkpoint["pending"] = tool_message("create_draft", {"name": "Uncertain tool"})["tool_calls"]
        agent._save(path, checkpoint)
        response = self.client.post(f"{self.root}/{self.run_id}/recover")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "interrupted")
        self.assertFalse(response.json()["can_resume"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_interrupted_edit_reconciles_saved_effect_without_replaying_tool(self):
        self.create()
        self.tool("create_draft")
        self.complete.return_value = (tool_message("write_draft_file", {
            "path": "endpoints/recovered.py", "content": "def recovered():\n    return 1\n"}), None)
        self.step()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["status"] = "interrupted"
        checkpoint["active_tool"] = {"id": checkpoint["pending"][0]["id"],
            "name": "write_draft_file", "revision": checkpoint["revision"]}
        agent._save(path, checkpoint)
        drafts.write_draft_file(self.project.id, self.run["draft_id"],
            "endpoints/recovered.py", "def recovered():\n    return 1\n", self.run["revision"])
        with patch.object(agent, "_session", side_effect=AssertionError("tool must not replay")):
            recovered = self.step()
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["events"][-1]["status"], "done")
        self.assertEqual(recovered["revision"], drafts.inspect_draft(self.project.id, self.run["draft_id"])["revision"])
        self.assertEqual(agent._read(path)["pending"], [])

    def test_interrupted_edit_without_effect_returns_feedback_to_model(self):
        self.create()
        self.tool("create_draft")
        self.complete.return_value = (tool_message("write_draft_file", {
            "path": "endpoints/recovered.py", "content": "def recovered():\n    return 1\n"}), None)
        self.step()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["status"] = "interrupted"
        checkpoint["active_tool"] = {"id": checkpoint["pending"][0]["id"],
            "name": "write_draft_file", "revision": checkpoint["revision"]}
        agent._save(path, checkpoint)
        with patch.object(agent, "_session", side_effect=AssertionError("tool must not replay")):
            recovered = self.step()
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["events"][-1]["status"], "error")
        self.assertIn("tool_interrupted", agent._read(path)["messages"][-1]["content"])
        self.assertFalse((drafts.draft_directory(get_project(self.project.id), self.run["draft_id"])
                          / "endpoints/recovered.py").exists())

    def test_legacy_interrupted_route_is_reconciled_from_saved_manifest(self):
        self.create()
        self.tool("create_draft")
        self.tool("write_draft_file", path="endpoints/recovered.py",
                  content="def recovered():\n    return 1\n")
        self.complete.return_value = (tool_message("upsert_endpoint", {"endpoint": json.dumps({
            "method": "GET", "path": "/recovered", "handler": "endpoints/recovered.py:recovered"})}), None)
        self.step()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["status"] = "interrupted"
        agent._save(path, checkpoint)
        drafts.upsert_endpoint(self.project.id, self.run["draft_id"],
            {"method": "GET", "path": "/recovered", "handler": "endpoints/recovered.py:recovered"},
            self.run["revision"])
        with patch.object(agent, "_session", side_effect=AssertionError("tool must not replay")):
            recovered = self.step()
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["events"][-1]["status"], "done")
        self.assertEqual(agent._read(path)["pending"], [])

    def test_pending_batch_runs_sequentially_and_survives_reloading(self):
        self.create()
        first = tool_message("get_project_context", {"project_id": self.project.id, "include_source": False})
        second = tool_message("create_draft", {"project_id": self.project.id})
        first["tool_calls"].extend(second["tool_calls"])
        self.complete.return_value = (first, None)
        self.step()
        self.assertEqual(self.run["events"], [])
        self.step()
        self.assertIsNone(self.run["draft_id"])
        self.step()
        self.assertIsNotNone(self.run["draft_id"])
        self.assertEqual(self.complete.call_count, 1)
        self.assertEqual(len(drafts.list_drafts(self.project.id)["drafts"]), 1)

    def test_failed_checks_return_feedback_and_edits_clear_results(self):
        self.create(allow_execution=True)
        self.tool("create_draft")
        self.tool("write_draft_file", path="endpoints/broken.py", content="def broken():\n    return unknown_name\n", expected_revision=self.run["revision"])
        self.tool("validate_draft", expected_revision=self.run["revision"])
        self.assertEqual(self.run["events"][-1]["status"], "failed")
        self.assertIn("unknown_name", self.run["events"][-1]["detail"])
        self.assertNotEqual(self.run["checks"]["validate_draft"]["status"], "passed")
        self.tool("write_draft_file", path="endpoints/broken.py", content="def broken():\n    return 1\n", expected_revision=self.run["revision"])
        self.assertEqual(self.run["checks"], {})
        messages = self.complete.call_args.args[0]
        self.assertTrue(any(m["role"] == "tool" and 'failed' in m["content"] for m in messages))

    def test_invalid_python_is_rejected_with_line_before_saving(self):
        self.create()
        self.tool("create_draft")
        revision = self.run["revision"]
        self.tool("write_draft_file", path="endpoints/broken.py", content="def broken(:\n")
        self.assertEqual(self.run["events"][-1]["status"], "error")
        self.assertIn("line 1", self.run["events"][-1]["detail"])
        feedback = json.loads(agent._read(agent._path(self.project.id, self.run_id))["messages"][-1]["content"])
        self.assertEqual(feedback["code"], "source_syntax")
        self.assertEqual(feedback["line"], 1)
        self.assertEqual(drafts.inspect_draft(self.project.id, self.run["draft_id"])["revision"], revision)

    def test_static_failure_without_execution_is_fed_back_and_blocks_success_claim(self):
        self.create(allow_execution=False)
        self.tool("create_draft")
        self.tool("write_draft_file", path="endpoints/broken.py",
                  content="def broken():\n    return missing_name\n")
        self.assertIn("Static check", self.run["events"][-1]["detail"])
        self.complete.return_value = ({"role": "assistant", "content": "Done"}, None)
        self.assertEqual(self.step()["status"], "ready")
        self.assertIn("latest draft check failed", self.complete.call_args.args[0][0]["content"])
        self.assertEqual(self.step()["status"], "failed")
        self.assertEqual(self.run["error"]["code"], "repair_exhausted")

    def test_static_failure_without_execution_can_be_repaired_in_same_draft(self):
        self.create(allow_execution=False)
        self.tool("create_draft")
        self.tool("write_draft_file", path="endpoints/broken.py",
                  content="def broken():\n    return missing_name\n")
        self.complete.return_value = (tool_message("write_draft_file", {
            "path": "endpoints/broken.py", "content": "def broken():\n    return 1\n"}), None)
        self.step()
        self.assertIn("latest draft check failed", self.complete.call_args.args[0][0]["content"])
        self.step()
        self.assertEqual(self.run["status"], "ready")
        self.assertNotIn("Static check", self.run["events"][-1].get("detail", ""))
        self.complete.return_value = ({"role": "assistant", "content": "Saved. Execution was disabled."}, None)
        self.assertEqual(self.step()["status"], "completed")
        self.assertFalse(self.run["allow_execution"])

    def test_final_answer_automatically_validates_and_tests_edited_draft(self):
        self.create(allow_execution=True)
        self.tool("create_draft")
        self.tool("write_draft_file", path="endpoints/greetings.py", content=HANDLER)
        self.tool("write_draft_file", path="tests/test_greetings.py", content=TEST_SOURCE)
        self.tool("upsert_endpoint", endpoint={"method": "GET", "path": "/greet", "handler": "endpoints/greetings.py:greet"})
        self.complete.return_value = ({"role": "assistant", "content": "Greeting is ready."}, None)
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.run["next_action"], {"kind": "tool", "name": "validate_draft"})
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.run["next_action"], {"kind": "tool", "name": "test_draft"})
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.run["checks"]["validate_draft"]["status"], "passed")
        self.assertEqual(self.run["checks"]["test_draft"]["status"], "passed")
        self.assertEqual(self.step()["status"], "completed")

    def test_failed_automatic_check_is_fed_back_for_code_repair(self):
        self.create(allow_execution=True)
        self.tool("create_draft")
        self.tool("write_draft_file", path="endpoints/broken.py", content="def broken():\n    return unknown_name\n")
        self.complete.return_value = ({"role": "assistant", "content": "Done"}, None)
        self.assertEqual(self.step()["next_action"], {"kind": "tool", "name": "validate_draft"})
        self.assertEqual(self.step()["events"][-1]["status"], "failed")
        self.complete.return_value = (tool_message("write_draft_file", {
            "path": "endpoints/broken.py", "content": "def broken():\n    return 1\n"}), None)
        self.assertEqual(self.step()["status"], "ready")
        sent = self.complete.call_args.args[0]
        self.assertIn("latest draft check failed", sent[0]["content"])
        self.assertTrue(any(m["role"] == "tool" and "unknown_name" in m["content"] for m in sent))
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.run["checks"], {})
        self.complete.return_value = ({"role": "assistant", "content": "Fixed"}, None)
        self.assertEqual(self.step()["next_action"], {"kind": "tool", "name": "validate_draft"})
        self.assertEqual(self.step()["next_action"], {"kind": "tool", "name": "test_draft"})
        self.assertEqual(self.step()["status"], "ready")
        self.assertEqual(self.step()["status"], "completed")

    def test_selected_draft_revision_pair_and_run_ids_are_validated(self):
        self.assertEqual(self.client.put(f"{self.root}/{self.run_id}", json={"model": "test-tool-model", "message": "Change", "draft_id": "a" * 32}).status_code, 422)
        self.assertEqual(self.client.put(f"{self.root}/bad", json={"model": "test-tool-model", "message": "Change"}).status_code, 409)
        self.assertEqual(self.client.get(f"{self.root}/{'a' * 32}").status_code, 404)

    def finish_with_question(self):
        self.complete.return_value = ({"role": "assistant", "content": "Should GET /books return **an object** or an array?"}, None)
        self.step()
        self.assertTrue(self.run["can_reply"])

    def reply_body(self, **changes):
        return {"message": 'Return {"books": [], "count": 0}; preserve nonempty results.',
                "reply_id": uuid4().hex, "expected_turn": self.run["turn"],
                "expected_revision": self.run["revision"], **changes}

    def post_reply(self, body):
        return self.client.post(f"{self.root}/{self.run_id}/replies", json=body)

    def test_clarification_reply_keeps_draft_context_permissions_and_budget(self):
        self.create()
        self.tool("create_draft")
        draft_id = self.run["draft_id"]
        self.finish_with_question()
        original = agent._read(agent._path(self.project.id, self.run_id))
        body = self.reply_body()
        response = self.post_reply(body)
        self.assertEqual(response.status_code, 200, response.text)
        continued = response.json()
        self.assertEqual(continued["draft_id"], draft_id)
        self.assertEqual(continued["model_calls"], original["model_calls"])
        self.assertEqual(continued["tool_calls"], original["tool_calls"])
        self.assertFalse(continued["allow_execution"])
        self.assertEqual(continued["turn"], 1)
        self.assertIsNone(continued["answer"])
        self.assertTrue(any(event["kind"] == "user" and event["text"] == body["message"] for event in continued["events"]))
        self.tool("write_draft_file", path="endpoints/greetings.py", content=HANDLER,
                  expected_revision=continued["revision"])
        messages = self.complete.call_args.args[0]
        self.assertEqual(messages[-1], {"role": "user", "content": body["message"]})
        self.assertTrue(any("Should GET /books" in (m.get("content") or "") for m in messages))
        self.assertEqual(len(drafts.list_drafts(self.project.id)["drafts"]), 1)
        self.assertEqual(self.run["draft_id"], draft_id)

    def test_reply_without_draft_can_create_one_after_the_answer(self):
        self.create()
        self.finish_with_question()
        self.assertEqual(self.post_reply(self.reply_body()).status_code, 200)
        self.tool("create_draft")
        self.assertIsNotNone(self.run["draft_id"])
        self.assertEqual(len(drafts.list_drafts(self.project.id)["drafts"]), 1)

    def test_reply_idempotence_survives_lost_response_and_rejects_other_tabs(self):
        self.create()
        self.finish_with_question()
        body = self.reply_body()
        first = self.post_reply(body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.post_reply(body).json(), first.json())
        self.assertEqual(self.post_reply({**body, "message": "Different"}).status_code, 409)
        self.assertEqual(self.post_reply({**body, "reply_id": uuid4().hex}).status_code, 409)
        checkpoint = agent._read(agent._path(self.project.id, self.run_id))
        self.assertEqual(sum(m["role"] == "user" for m in checkpoint["messages"]), 2)

    def test_reply_rejects_changed_code_and_cannot_expand_execution_permissions(self):
        self.create()
        self.tool("create_draft")
        self.finish_with_question()
        body = self.reply_body()
        self.assertEqual(self.post_reply({**body, "allow_execution": True}).status_code, 422)
        self.assertEqual(self.post_reply({**body, "expected_turn": 99}).status_code, 409)
        drafts.write_draft_file(self.project.id, self.run["draft_id"],
                               "endpoints/other.py", "value=1\n", self.run["revision"])
        self.assertEqual(self.post_reply(body).status_code, 409)
        checkpoint = agent._read(agent._path(self.project.id, self.run_id))
        self.assertEqual(sum(m["role"] == "user" for m in checkpoint["messages"]), 1)

    def test_reply_never_resets_exhausted_request_budgets(self):
        self.create()
        self.assertEqual(self.post_reply(self.reply_body()).status_code, 409)
        self.finish_with_question()
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["model_calls"] = agent.MAX_MODEL_CALLS
        agent._save(path, checkpoint)
        self.assertEqual(self.post_reply(self.reply_body()).status_code, 409)
        self.assertFalse(self.client.get(f"{self.root}/{self.run_id}").json()["can_reply"])

    def test_missing_api_contract_has_actionable_detail_in_new_and_existing_runs(self):
        self.create()
        self.tool("create_draft")
        self.tool("get_api_schema", method="GET", path="/hello")
        event = self.run["events"][-1]
        self.assertEqual(event["status"], "error")
        self.assertIn("Validate", event["detail"])
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["events"][-1].pop("detail")  # Old checkpoints have no public explanation.
        agent._save(path, checkpoint)
        self.assertEqual(self.client.get(f"{self.root}/{self.run_id}").json()["events"][-1]["detail"], event["detail"])

    def test_concurrent_steps_cannot_reserve_or_replay_the_same_call(self):
        self.create()
        entered, release = Event(), Event()

        def model(*args, **kwargs):
            entered.set()
            release.wait(5)
            return tool_message("create_draft", {"project_id": self.project.id}), None

        self.complete.side_effect = model
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.step)
            try:
                self.assertTrue(entered.wait(5))
                response = self.client.post(f"{self.root}/{self.run_id}/step")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"]["code"], "generation_busy")
            finally:
                release.set()
            self.assertEqual(pending.result()["status"], "ready")
        self.complete.assert_called_once()
        self.step()
        self.assertEqual(len(drafts.list_drafts(self.project.id)["drafts"]), 1)

    def test_malformed_tool_response_does_not_execute_and_prompt_limit_is_terminal(self):
        self.create()
        self.complete.return_value = ({"role": "assistant", "content": None,
                                       "tool_calls": [{"type": "custom", "id": "x"}]}, None)
        self.assertEqual(self.step()["status"], "failed")
        self.assertEqual(self.run["tool_calls"], 0)
        with patch.object(agent, "MAX_PROMPT_BYTES", 1):
            self.assertEqual(self.step()["status"], "limit")
        self.assertFalse(self.run["can_resume"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])


    def test_retry_context_explains_truncation_without_replaying_invalid_response(self):
        self.create(allow_edits=False)
        self.complete.side_effect = GenerationError("Model output was truncated", diagnostics={
            "finish_reason": "length", "usage": {"total_tokens": 20}})
        self.assertEqual(self.step()["status"], "failed")
        self.complete.side_effect = None
        self.complete.return_value = ({"role": "assistant", "content": "Here is the explanation"}, None)
        self.assertEqual(self.step()["status"], "completed")
        messages = self.complete.call_args.args[0]
        self.assertIn("Output was truncated", messages[0]["content"])
        self.assertIn("one file per call", messages[0]["content"])
        self.assertEqual([m for m in messages if m["role"] == "user"],
                         [{"role": "user", "content": "Add reader greetings"}])
        self.assertEqual(len(messages), 2)

    def test_failed_turn_accepts_correction_and_retains_unverified_edits(self):
        self.create()
        self.complete.return_value = (tool_message("create_draft", {"name": "Greeting fixes"}), None)
        self.step()
        self.step()
        path = agent._path(self.project.id, self.run_id)
        saved = agent._read(path)
        saved.update(status="failed", edits_made=True, repair_required=True,
                     error={"message": "Checks need repair"})
        agent._save(path, saved)
        public = self.client.get(f"{self.root}/{self.run_id}").json()
        self.assertTrue(public["can_reply"])
        response = self.client.post(f"{self.root}/{self.run_id}/replies", json={
            "message": "Preserve auth and fix the missing import", "reply_id": uuid4().hex,
            "expected_turn": public["turn"], "expected_revision": public["revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        saved = agent._read(path)
        self.assertTrue(saved["edits_made"])
        self.assertTrue(saved["repair_required"])
        self.assertEqual(saved["draft_id"], public["draft_id"])
        self.assertEqual(saved["messages"][-1]["content"], "Preserve auth and fix the missing import")


class ToolProviderTests(unittest.TestCase):
    def setUp(self):
        ProviderFixture.setUp(self)

    def test_tools_have_no_json_response_format_and_preserve_call_ids(self):
        from reqlica.generation import provider
        from pydantic import SecretStr
        self.payload["choices"][0].update(finish_reason="tool_calls", message=tool_message("create_draft", {"project_id": "a" * 32}))
        tools = [{"type": "function", "function": {"name": "create_draft", "parameters": {"type": "object", "properties": {}}}}]
        result, _ = provider.complete([{"role": "user", "content": "Build"}], self.request, SecretStr("private-key"), tools=tools)
        self.assertEqual(result["tool_calls"][0]["id"], self.payload["choices"][0]["message"]["tool_calls"][0]["id"])
        wire = json.loads(self.requests[0].content)
        self.assertEqual(wire["tools"], tools)
        self.assertFalse(wire["parallel_tool_calls"])
        self.assertNotIn("response_format", wire)
        self.assertEqual(len(wire["messages"]), 1)

    def openrouter_request(self):
        return self.request.model_copy(update={
            "provider": "compatible", "model": "minimax/minimax-m3.1-flash:free",
            "base_url": "https://openrouter.ai/api/v1",
        })

    def tool_complete(self, request):
        from reqlica.generation import provider
        from pydantic import SecretStr
        return provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[{
            "type": "function", "function": {"name": "create_draft", "parameters": {"type": "object", "properties": {}}},
        }])

    def test_openrouter_tool_model_does_not_require_unsupported_parallel_control(self):
        # Public metadata for the reported MiniMax model: tools/tool_choice are
        # supported, but parallel_tool_calls is not. Requiring the latter filters
        # its available provider out and produces an OpenRouter HTTP 404.
        with patch("reqlica.generation.provider.openrouter_model", return_value={
            "supported_parameters": ["tools", "tool_choice", "max_tokens", "temperature", "reasoning"],
        }):
            self.tool_complete(self.openrouter_request())
        wire = json.loads(self.requests[0].content)
        self.assertNotIn("parallel_tool_calls", wire)
        self.assertEqual(wire["tool_choice"], "auto")
        self.assertNotIn("provider", wire)
        self.assertIn("tools", wire)
        self.assertEqual(str(self.requests[0].url), "https://openrouter.ai/api/v1/chat/completions")

    def test_openrouter_optional_controls_require_advertised_support(self):
        with patch("reqlica.generation.provider.openrouter_model", return_value={
            "supported_parameters": ["tools", "parallel_tool_calls"],
        }):
            self.tool_complete(self.openrouter_request())
        wire = json.loads(self.requests[0].content)
        self.assertFalse(wire["parallel_tool_calls"])
        self.assertNotIn("tool_choice", wire)

    def test_unknown_compatible_provider_omits_optional_tool_controls(self):
        request = self.request.model_copy(update={"provider": "compatible", "model": "local",
                                                   "base_url": "http://127.0.0.1:1234/v1"})
        with patch("reqlica.generation.provider.openrouter_model") as catalog:
            self.tool_complete(request)
        catalog.assert_not_called()
        wire = json.loads(self.requests[0].content)
        self.assertNotIn("parallel_tool_calls", wire)
        self.assertNotIn("tool_choice", wire)
        self.assertIn("tools", wire)

    def test_openrouter_model_without_tools_is_rejected_before_inference(self):
        with patch("reqlica.generation.provider.openrouter_model", return_value={"supported_parameters": ["response_format"]}):
            with self.assertRaises(GenerationError) as raised:
                self.tool_complete(self.openrouter_request())
        self.assertEqual(raised.exception.detail["code"], "tools_unsupported")
        self.assertEqual(self.requests, [])

    def test_openrouter_404_explains_parameter_routing_without_leaking_raw_content(self):
        self.status = 404
        self.payload = {"error": {"code": 404, "message": "No endpoints found that support all requested parameters. private-provider-key private-source"}}
        with patch("reqlica.generation.provider.openrouter_model", return_value={"supported_parameters": ["tools"]}):
            with self.assertRaises(GenerationError) as raised:
                self.tool_complete(self.openrouter_request())
        message = raised.exception.detail["message"]
        self.assertIn("No OpenRouter endpoint supports all requested parameters", message)
        self.assertNotIn("private-provider-key", json.dumps(raised.exception.detail))
        self.assertNotIn("private-source", json.dumps(raised.exception.detail))
        self.assertEqual(len(self.requests), 1)

    def test_generic_404_identifies_model_or_endpoint_configuration(self):
        self.status = 404
        self.payload = {"error": {"message": "Unknown model private-source"}}
        with self.assertRaises(GenerationError) as raised:
            self.tool_complete(self.request)
        self.assertIn("model identifier and API base URL", raised.exception.detail["message"])
        self.assertNotIn("private-source", json.dumps(raised.exception.detail))
