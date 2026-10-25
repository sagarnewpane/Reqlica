"""Prompt reductions preserve requirements, source strings and validation rules."""

from copy import deepcopy
import json
import unittest

from jsonschema import Draft202012Validator
from reqlica.generation.context import model_messages, model_tool_schema


class AgentContextTests(unittest.TestCase):
    def test_scope_and_revision_are_bound_but_business_constraints_remain(self):
        schema = {"type": "object", "title": "Tool", "additionalProperties": False,
            "required": ["project_id", "draft_id", "expected_revision", "endpoint"],
            "properties": {
                "project_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                "draft_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                "expected_revision": {"type": "string", "pattern": "^[a-f0-9]{64}$", "description": "Latest revision; do not invent one."},
                "endpoint": {"$ref": "#/$defs/Endpoint"}},
            "$defs": {"Endpoint": {"title": "Endpoint", "type": "object", "required": ["method", "path"], "properties": {
                "method": {"title": "Method", "type": "string", "enum": ["GET", "POST"]},
                "path": {"title": "Path", "type": "string", "pattern": "^/", "description": "Registered path, not a full URL."},
                "count": {"type": "integer", "minimum": 1, "maximum": 100},
                "title": {"type": "string", "minLength": 2, "default": "title remains data"}}}}}
        before = deepcopy(schema)
        projected = model_tool_schema(schema)
        self.assertEqual(schema, before)
        self.assertNotIn("project_id", projected["properties"])
        self.assertNotIn("draft_id", projected["required"])
        self.assertNotIn("expected_revision", projected["required"])
        self.assertNotIn("expected_revision", projected["properties"])
        validator = Draft202012Validator(projected)
        valid = {"endpoint": {"method": "GET", "path": "/books", "title": "Books", "count": 5}}
        self.assertTrue(validator.is_valid(valid))
        for changed in ({"endpoint": {"method": "DELETE", "path": "/books"}},
                        {"endpoint": {"method": "GET", "path": "https://example.com"}},
                        {"endpoint": {"method": "GET", "path": "/books", "title": "x"}},
                        {"endpoint": {"method": "GET", "path": "/books", "count": 101}}):
            self.assertFalse(validator.is_valid({**valid, **changed}))
        self.assertEqual(projected["$defs"]["Endpoint"]["properties"]["path"]["description"], "Registered path, not a full URL.")

    def test_annotations_do_not_remove_properties_or_literal_data_named_title(self):
        literal = {"title": "keep", "properties": {"title": "literal value"}, "description": "keep this"}
        schema = {"type": "object", "title": "Annotation", "properties": {
            "title": {"title": "Human label", "type": "string", "examples": ["Sample"]},
            "properties": {"type": "object", "default": literal, "examples": [literal], "const": literal},
            "description": {"type": "string"},
            "optional": {"anyOf": [{"title": "First", "type": "null"}, {"title": "Second", "type": "integer", "minimum": 2}]}}}
        result = model_tool_schema(schema)
        self.assertEqual(set(result["properties"]), set(schema["properties"]))
        self.assertNotIn("title", result["properties"]["title"])
        self.assertEqual(result["properties"]["properties"]["default"], literal)
        self.assertEqual(result["properties"]["properties"]["examples"], [literal])
        self.assertEqual(result["properties"]["properties"]["const"], literal)
        self.assertEqual(result["properties"]["optional"]["anyOf"][1]["minimum"], 2)

    def test_json_projection_preserves_full_source_requirements_and_call_ids(self):
        source = 'def hello():\n    return {"title": "こんにちは", "spaces": "a  b"}\n'
        payload = {"source": {"endpoints/hello.py": source}, "revision": "a" * 64, "checks": {"status": "passed", "tests_run": 3}, "null": None, "number": 0.25}
        messages = [{"role": "user", "content": "Keep these spaces: a  b\nAnd every requirement."},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "get_project_context", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": json.dumps(payload, indent=2)},
            {"role": "assistant", "content": "An earlier clarification and decision."}]
        before = deepcopy(messages)
        projected = model_messages(messages)
        self.assertEqual(messages, before)
        self.assertEqual(len(projected), len(messages))
        self.assertEqual(projected[:2], messages[:2])
        self.assertEqual(projected[3], messages[3])
        self.assertEqual(projected[2]["tool_call_id"], "call-1")
        self.assertEqual(json.loads(projected[2]["content"]), payload)
        self.assertEqual(json.loads(projected[2]["content"])["source"]["endpoints/hello.py"], source)
        self.assertLess(len(projected[2]["content"].encode()), len(messages[2]["content"].encode()))

    def test_ambiguous_malformed_or_nonfinite_tool_results_are_not_rewritten(self):
        for text in ('raw tool text', '{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}', '{invalid', '{"x":1}'):
            original = [{"role": "tool", "tool_call_id": "id", "content": text}]
            self.assertEqual(model_messages(original), original)

    def test_old_source_writes_are_omitted_but_latest_and_checkpoint_stay_intact(self):
        source = "def greet():\n    return 'hello'\n" * 200
        def call(identifier, path):
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": identifier, "type": "function", "function": {
                    "name": "write_draft_file", "arguments": json.dumps({
                        "path": path, "expected_revision": "a" * 64, "content": source})}}]}
        messages = [
            {"role": "user", "content": "Add greetings"},
            call("first", "endpoints/first.py"),
            {"role": "tool", "tool_call_id": "first", "content": '{"revision":"b"}'},
            call("second", "endpoints/second.py"),
            {"role": "tool", "tool_call_id": "second", "content": '{"error":"stale revision"}'},
        ]
        before = deepcopy(messages)
        projected = model_messages(messages)
        self.assertEqual(messages, before)
        self.assertEqual(len(projected), len(messages))
        old = json.loads(projected[1]["tool_calls"][0]["function"]["arguments"])
        latest = json.loads(projected[3]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(old["path"], "endpoints/first.py")
        self.assertEqual(old["expected_revision"], "a" * 64)
        self.assertIn("omitted", old["content"])
        self.assertEqual(latest["content"], source)
        self.assertEqual(projected[2:3], messages[2:3])
        self.assertEqual(projected[4:], messages[4:])
        self.assertLess(len(json.dumps(projected).encode()), len(json.dumps(messages).encode()) * 0.6)

    def test_old_failed_check_keeps_actionable_feedback_without_replaying_long_logs(self):
        payload = {"status": "failed", "revision": "a" * 64,
                   "output": "setup noise\n" * 2500 + "NameError: missing_name",
                   "failures": [{"test": f"case_{index}", "output": "trace\n" * 500 + "NameError: missing_name"}
                                for index in range(8)]}
        messages = [{"role": "assistant", "content": None, "tool_calls": [{
            "id": "check-1", "type": "function", "function": {"name": "validate_draft", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "check-1", "content": json.dumps(payload)}]
        projected = model_messages(messages)
        result = json.loads(projected[1]["content"])
        self.assertEqual(len(result["failures"]), 3)
        self.assertEqual(result["more_failures"], 5)
        self.assertIn("NameError: missing_name", result["output"])
        self.assertLess(len(projected[1]["content"]), 7000)
        self.assertEqual(json.loads(messages[1]["content"]), payload)

    def test_repeated_project_reads_keep_only_latest_source_for_each_path(self):
        def call(identifier):
            return {"role": "assistant", "content": None, "tool_calls": [{"id": identifier,
                "type": "function", "function": {"name": "get_project_context", "arguments": "{}"}}]}
        messages = [call("first"), {"role": "tool", "tool_call_id": "first", "content": json.dumps({
            "revision": "old", "source": {"endpoints/a.py": "old" * 3000, "endpoints/b.py": "keep"}})},
            call("second"), {"role": "tool", "tool_call_id": "second", "content": json.dumps({
                "revision": "new", "source": {"endpoints/a.py": "new" * 3000}})}]
        projected = model_messages(messages)
        old = json.loads(projected[1]["content"])
        self.assertEqual(old["source"], {"endpoints/b.py": "keep"})
        self.assertEqual(json.loads(projected[3]["content"])["source"]["endpoints/a.py"], "new" * 3000)
        self.assertLess(len(projected[1]["content"]), 300)

    def test_preview_validation_details_are_preserved(self):
        payload = {"status": "failed", "validation": {"issues": [{"path": "rows.0.user_id",
                   "message": "Unknown reference"}]}}
        messages = [{"role": "assistant", "content": None, "tool_calls": [{"id": "preview-1",
            "type": "function", "function": {"name": "preview_mock_data", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "preview-1", "content": json.dumps(payload)}]
        self.assertEqual(json.loads(model_messages(messages)[1]["content"]), payload)

class ContextSelectionTests(unittest.TestCase):
    def exchange(self, identifier, name, arguments, payload):
        return [{"role": "assistant", "content": None, "tool_calls": [{"id": identifier,
                 "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
                {"role": "tool", "tool_call_id": identifier, "content": json.dumps(payload)}]

    def test_new_turn_preserves_requirements_and_answers_without_historical_source(self):
        from reqlica.generation.context import select_messages
        messages = [{"role": "user", "content": "Keep auth and use a  b spacing. 日本語"}]
        messages += self.exchange("read", "get_project_context", {}, {"revision": "old", "source": {"endpoints/a.py": "stale source" * 10000}})
        messages += self.exchange("write", "write_draft_file", {"path": "endpoints/a.py", "content": "saved code" * 5000}, {"status": "saved", "revision": "new"})
        messages += [{"role": "assistant", "content": "Added auth. Still need pagination."},
                     {"role": "user", "content": "Now add pagination; preserve auth."}]
        before = deepcopy(messages)
        projected = select_messages(messages)
        self.assertEqual(messages, before)
        self.assertEqual([m for m in projected if m["role"] == "user"], [messages[0], messages[-1]])
        self.assertIn("Still need pagination", projected[-2]["content"])
        self.assertNotIn("stale source", json.dumps(projected))
        self.assertNotIn("saved code", json.dumps(projected))
        self.assertLess(len(json.dumps(projected)), 3000)

    def test_active_rejected_write_keeps_unsaved_source_and_structured_errors(self):
        from reqlica.generation.context import select_messages
        source = "def broken():\n return missing\n" * 100
        messages = [{"role": "user", "content": "Fix the API"}] + self.exchange("write", "write_draft_file",
            {"path": "endpoints/a.py", "content": source}, {"error": "Invalid source", "path": "endpoints/a.py", "line": 2,
                "errors": [{"loc": ["handler"], "msg": "Undefined name", "type": "name_error"}]})
        projected = select_messages(messages)
        self.assertEqual(json.loads(projected[1]["tool_calls"][0]["function"]["arguments"])["content"], source)
        feedback = json.loads(projected[2]["content"])
        self.assertEqual(feedback["errors"][0]["loc"], ["handler"])
        self.assertEqual(feedback["line"], 2)

    def test_budget_evicts_complete_exchanges_without_orphaning_tool_results(self):
        from reqlica.generation.context import select_messages
        messages = [{"role": "user", "content": "Keep all requirements"}]
        for i in range(30):
            messages += self.exchange(str(i), "get_project_context", {}, {"revision": str(i), "source": {f"endpoints/{i}.py": "x" * 5000}})
        messages += [{"role": "user", "content": "Continue pending work"}]
        projected = select_messages(messages, max_bytes=3000)
        self.assertLessEqual(len(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode()), 3000)
        ids = {c["id"] for m in projected for c in m.get("tool_calls", [])}
        outputs = {m["tool_call_id"] for m in projected if m["role"] == "tool"}
        self.assertEqual(ids, outputs)
        self.assertEqual(projected[0], messages[0])
        self.assertEqual(projected[-1], messages[-1])

    def test_requirements_over_budget_fail_without_silently_cutting_user_intent(self):
        from reqlica.generation.context import select_messages
        from reqlica.generation.errors import GenerationError
        messages = [{"role": "user", "content": "日本語" * 1000}]
        with self.assertRaises(GenerationError) as error:
            select_messages(messages, max_bytes=1000)
        self.assertEqual(error.exception.detail["code"], "prompt_limit")
        self.assertEqual(messages[0]["content"], "日本語" * 1000)


    def test_read_source_is_invalidated_by_a_later_successful_write(self):
        from reqlica.generation.context import select_messages
        messages = [{"role": "user", "content": "Update A"}]
        messages += self.exchange("read", "get_project_context", {}, {"revision": "old", "source": {"endpoints/a.py": "obsolete", "endpoints/b.py": "still current"}})
        messages += self.exchange("write", "write_draft_file", {"path": "endpoints/a.py", "content": "replacement"}, {"revision": "new", "status": "saved"})
        projected = select_messages(messages)
        source = json.loads(projected[2]["content"])["source"]
        self.assertEqual(source, {"endpoints/b.py": "still current"})
        self.assertNotIn("replacement", json.dumps(projected))


    def test_large_previous_answer_is_abridged_before_refusing_a_new_request(self):
        from reqlica.generation.context import select_messages
        messages = [{"role": "user", "content": "Keep auth"},
                    {"role": "assistant", "content": "Auth kept. " + "verbose history " * 4000 + "Pagination still pending."},
                    {"role": "user", "content": "Now finish pagination"}]
        projected = select_messages(messages, max_bytes=3000)
        self.assertEqual(projected[0], messages[0])
        self.assertEqual(projected[-1], messages[-1])
        self.assertIn("Auth kept", projected[1]["content"])
        self.assertIn("Pagination still pending", projected[1]["content"])
        self.assertIn("[omitted]", projected[1]["content"])
