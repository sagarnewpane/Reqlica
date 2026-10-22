"""Real MCP client/server integration with deterministic model tool calls."""

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
from reqlica.projects import drafts
from reqlica.projects.store import create_project, get_project, registry_directory
from reqlica.projects.versions import code_directory, content_digest, atomic_write_json, snapshot_code
from test_generation import HANDLER, TEST_SOURCE
import test_generation as generation_tests


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
        response = self.client.put(f"{self.root}/{self.run_id}", json={"message": "Add reader greetings", **settings})
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
        rejected = self.client.put(f"{self.root}/{uuid4().hex}", json={"message": "Explain this"})
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
            ("write_draft_file", {"expected_revision": "f" * 64, "path": "endpoints/x.py", "content": "x=1"}),
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
            "message": "Continue", "draft_id": selected["id"],
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
        self.assertEqual(self.client.put(f"{self.root}/{self.run_id}", json={"message": "Different"}).status_code, 409)
        path = agent._path(self.project.id, self.run_id)
        checkpoint = agent._read(path)
        checkpoint["model_calls"] = agent.MAX_MODEL_CALLS
        agent._save(path, checkpoint)
        self.assertEqual(self.step()["status"], "limit")
        self.complete.assert_not_called()

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
        self.tool("write_draft_file", path="endpoints/broken.py", content="def broken(:\n", expected_revision=self.run["revision"])
        self.tool("validate_draft", expected_revision=self.run["revision"])
        self.assertEqual(self.run["events"][-1]["status"], "failed")
        self.assertNotEqual(self.run["checks"]["validate_draft"]["status"], "passed")
        self.tool("write_draft_file", path="endpoints/broken.py", content="def broken():\n    return 1\n", expected_revision=self.run["revision"])
        self.assertEqual(self.run["checks"], {})
        messages = self.complete.call_args.args[0]
        self.assertTrue(any(m["role"] == "tool" and 'failed' in m["content"] for m in messages))

    def test_selected_draft_revision_pair_and_run_ids_are_validated(self):
        self.assertEqual(self.client.put(f"{self.root}/{self.run_id}", json={"message": "Change", "draft_id": "a" * 32}).status_code, 422)
        self.assertEqual(self.client.put(f"{self.root}/bad", json={"message": "Change"}).status_code, 409)
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


class ToolProviderTests(unittest.TestCase):
    def setUp(self):
        generation_tests.GroqProviderTests.setUp(self)

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
        self.assertEqual(wire["provider"], {"require_parameters": True})
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
