"""Prompt reductions preserve requirements, source strings and validation rules."""

from copy import deepcopy
import json
import unittest

from jsonschema import Draft202012Validator
from reqlica.generation.context import model_messages, model_tool_schema


class AgentContextTests(unittest.TestCase):
    def test_scope_is_bound_but_revision_and_business_constraints_remain(self):
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
        self.assertIn("expected_revision", projected["required"])
        self.assertEqual(projected["properties"]["expected_revision"]["pattern"], "^[a-f0-9]{64}$")
        validator = Draft202012Validator(projected)
        valid = {"expected_revision": "a" * 64, "endpoint": {"method": "GET", "path": "/books", "title": "Books", "count": 5}}
        self.assertTrue(validator.is_valid(valid))
        for changed in ({"expected_revision": "stale"}, {"endpoint": {"method": "DELETE", "path": "/books"}},
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
