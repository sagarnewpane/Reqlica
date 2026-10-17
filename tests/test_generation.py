"""Single-turn generation saves checked proposals without executing model code."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI
from pydantic import SecretStr
from reqlica.console.app import create_app
from reqlica.generation import provider
from reqlica.generation.errors import GenerationError
from reqlica.generation.prompts import build_messages
from reqlica.generation.schemas import (
    GenerationRequest,
    ProjectAnswer,
    ProjectQuestionRequest,
)
from reqlica.generation.service import generate_endpoints
from reqlica.projects import drafts
from reqlica.projects.store import create_project
from reqlica.projects.versions import code_directory, content_digest

HANDLER = """from pydantic import BaseModel

class Greeting(BaseModel):
    message: str

def greet(name: str = "reader") -> Greeting:
    return Greeting(message="Hello, " + name)
"""
TEST_SOURCE = """import unittest
from endpoints.greetings import greet

class GreetingTests(unittest.TestCase):
    def test_named_reader(self):
        self.assertEqual(greet("Ada").message, "Hello, Ada")

    def test_default_reader(self):
        self.assertEqual(greet().message, "Hello, reader")
"""


def proposal():
    return {
        "summary": "Add a typed reader greeting without changing /hello",
        "assumptions": ["A greeting is stateless; no schema changes are needed."],
        "files": [
            {"path": "endpoints/greetings.py", "content": HANDLER},
            {"path": "tests/test_greetings.py", "content": TEST_SOURCE},
        ],
        "endpoints": [
            {
                "method": "GET",
                "path": "/greet",
                "handler": "endpoints/greetings.py:greet",
            }
        ],
    }


def json_members(pairs):
    """Serialize intentional duplicate keys without Python dicts discarding them."""
    return "{" + ",".join(json.dumps(k) + ":" + json.dumps(v) for k, v in pairs) + "}"


def split_file_proposal(plan=None):
    plan = plan or proposal()
    return json_members(
        [
            ("summary", plan["summary"]),
            ("assumptions", plan["assumptions"]),
            ("files", plan["files"][:1]),
            ("endpoints", plan["endpoints"]),
            ("files", plan["files"][1:]),
        ]
    )


class GenerationFixture:
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "books", "Books")
        self.request = GenerationRequest(
            requirements="Add a greeting for a named reader"
        )
        self.complete = self.enterContext(
            patch("reqlica.generation.service.provider.complete")
        )
        self.complete.return_value = (json.dumps(proposal()), {"total_tokens": 123})

    def generate(self):
        return generate_endpoints(self.project.id, self.request)


class GenerationTests(GenerationFixture, unittest.TestCase):
    def test_one_request_saves_fresh_draft_and_preserves_other_work(self):
        other = drafts.create_draft(self.project.id)
        before = content_digest(code_directory(self.project))
        database = (self.project.directory / "data.sqlite").read_bytes()
        result = self.generate()
        self.complete.assert_called_once()
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(result["context_revision"], before)
        self.assertNotEqual(result["draft"]["id"], other["id"])
        self.assertEqual(drafts.inspect_draft(self.project.id, other["id"]), other)
        self.assertIsNone(result["draft"]["validation"])
        self.assertIsNone(result["draft"]["test"])
        self.assertIsNone(result["draft"]["approval"])
        self.assertEqual(result["checks"], "not_run")
        self.assertEqual(content_digest(code_directory(self.project)), before)
        self.assertEqual(
            (self.project.directory / "data.sqlite").read_bytes(), database
        )
        draft = drafts.draft_directory(self.project, result["draft"]["id"])
        manifest = json.loads((draft / "manifest.json").read_text())
        self.assertEqual(
            {e["path"] for e in manifest["endpoints"]}, {"/hello", "/greet"}
        )
        self.assertEqual((draft / "endpoints/greetings.py").read_text(), HANDLER)

    def test_context_reuses_project_helper_without_history_records_or_env_files(self):
        (code_directory(self.project) / ".env").write_text(
            "PRIVATE_VALUE=not-for-model"
        )
        _, messages = build_messages(self.project.id, self.request)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        payload = json.loads(messages[1]["content"])
        self.assertIn("endpoints/hello.py", payload["source"])
        self.assertIn("tests/test_hello.py", payload["source"])
        self.assertIn("database_definitions", payload)
        self.assertNotIn("directory", payload["project"])
        self.assertNotIn("not-for-model", json.dumps(messages))
        self.assertNotIn("draft_index", payload)
        self.assertNotIn("history", payload)
        self.assertIn("transaction", messages[0]["content"])

    def test_generated_python_is_not_imported_or_run(self):
        plan = proposal()
        plan["files"][0]["content"] = (
            "raise RuntimeError('must not execute')\n" + HANDLER
        )
        self.complete.return_value = (json.dumps(plan), None)
        result = self.generate()
        self.assertIsNone(result["draft"]["validation"])
        self.assertIsNone(result["draft"]["test"])

    def test_human_can_validate_test_and_call_generated_endpoint_after_review(self):
        result = self.generate()
        args = (self.project.id, result["draft"]["id"], result["draft"]["revision"])
        self.assertEqual(drafts.validate_draft(*args)["status"], "passed")
        tests = drafts.test_draft(*args)
        self.assertEqual(tests["status"], "passed", tests)
        self.assertGreaterEqual(tests["tests_passed"], 3)
        call = drafts.call_draft_endpoint(*args, "GET", "/greet?name=Ada")
        self.assertEqual(call["status_code"], 200)
        self.assertEqual(call["body"], {"message": "Hello, Ada"})
        self.assertIsNone(drafts.inspect_draft(*args[:2])["approval"])

    def test_rejects_bad_outputs_before_creating_any_draft(self):
        cases = []
        for path in (
            "../outside.py",
            "/tmp/outside.py",
            "manifest.json",
            "project.json",
            ".reqlica/state.py",
            "endpoints/../outside.py",
            "endpoints//bad.py",
            "endpoints/./bad.py",
            "endpoints\\bad.py",
            "models/data.sqlite",
        ):
            plan = proposal()
            plan["files"][0]["path"] = path
            cases.append((path, plan))
        plan = proposal()
        plan["files"].append(copy.deepcopy(plan["files"][0]))
        cases.append(("duplicate file", plan))
        plan = proposal()
        plan["files"][0]["content"] = "def invalid(:"
        cases.append(("syntax", plan))
        plan = proposal()
        plan["endpoints"][0]["handler"] = "endpoints/greetings.py:missing"
        cases.append(("missing handler", plan))
        plan = proposal()
        plan["endpoints"].append(copy.deepcopy(plan["endpoints"][0]))
        cases.append(("duplicate route", plan))
        plan = proposal()
        plan["files"] = plan["files"][:1]
        cases.append(("no tests", plan))
        plan = proposal()
        plan["files"][0]["content"] = "#" * 65537
        cases.append(("large file", plan))
        plan = proposal()
        plan["files"][0]["path"] = "endpoints/HELLO.py"
        cases.append(("case collision", plan))
        plan = proposal()
        plan["approval"] = True
        cases.append(("approval injection", plan))
        plan = proposal()
        plan["files"] = []
        plan["endpoints"] = []
        cases.append(("no implementation", plan))
        for name, plan in cases:
            with self.subTest(name=name):
                self.complete.return_value = (json.dumps(plan), None)
                with self.assertRaises(GenerationError):
                    self.generate()
                self.assertEqual(drafts.list_drafts(self.project.id), {"drafts": []})

    def test_malformed_json_is_not_repaired_or_retried(self):
        self.complete.return_value = ("```json\n{}\n```", None)
        with self.assertRaises(GenerationError):
            self.generate()
        self.complete.assert_called_once()
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_split_handler_and_test_arrays_are_combined_without_losing_code(self):
        self.complete.return_value = (split_file_proposal(), None)
        result = self.generate()
        self.complete.assert_called_once()
        self.assertEqual(
            result["generated_files"],
            ["endpoints/greetings.py", "tests/test_greetings.py"],
        )
        directory = drafts.draft_directory(self.project, result["draft"]["id"])
        self.assertEqual((directory / "endpoints/greetings.py").read_text(), HANDLER)
        self.assertEqual(
            (directory / "tests/test_greetings.py").read_text(), TEST_SOURCE
        )
        self.assertEqual(len(result["output_warnings"]), 1)
        self.assertIn("Combined 2 top-level files arrays", result["output_warnings"][0])
        self.assertIsNone(result["draft"]["validation"])
        self.assertIsNone(result["draft"]["approval"])

    def test_repeated_disjoint_route_arrays_are_also_preserved(self):
        plan = proposal()
        extra_route = {**plan["endpoints"][0], "path": "/salute"}
        text = (
            json.dumps(plan)[:-1] + ', "endpoints": ' + json.dumps([extra_route]) + "}"
        )
        self.complete.return_value = (text, None)
        result = self.generate()
        self.assertEqual(
            [e["path"] for e in result["endpoints"]], ["/greet", "/salute"]
        )
        directory = drafts.draft_directory(self.project, result["draft"]["id"])
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(
            {e["path"] for e in manifest["endpoints"]}, {"/hello", "/greet", "/salute"}
        )
        self.assertIn("endpoints arrays", result["output_warnings"][0])

    def test_empty_repeated_arrays_do_not_erase_a_complete_proposal(self):
        text = json.dumps(proposal())[:-1] + ', "files": [], "endpoints": []}'
        self.complete.return_value = (text, None)
        result = self.generate()
        self.assertEqual(len(result["generated_files"]), 2)
        self.assertEqual(len(result["endpoints"]), 1)
        self.assertEqual(len(result["output_warnings"]), 2)

    def test_ambiguous_duplicate_metadata_and_nested_members_still_fail(self):
        base = json.dumps(proposal())[:-1]
        nested_file = (
            '{"path":"endpoints/greetings.py","path":"../outside.py",'
            '"content":' + json.dumps(HANDLER) + "}"
        )
        nested_content = (
            '{"path":"endpoints/greetings.py","content":'
            + json.dumps(HANDLER)
            + ',"content":"other private source"}'
        )
        nested_route = (
            '{"method":"GET","path":"/other",'
            '"handler":"endpoints/greetings.py:greet",'
            '"handler":"endpoints/greetings.py:missing"}'
        )
        cases = [
            base + ', "summary": "different summary"}',
            base + ', "summary": ' + json.dumps(proposal()["summary"]) + "}",
            base + ', "assumptions": []}',
            base + ', "files": null}',
            base + ', "files": {}}',
            base + ', "endpoints": "not an array"}',
            base + ', "files": [' + nested_file + "]}",
            base + ', "files": [' + nested_content + "]}",
            base + ', "endpoints": [' + nested_route + "]}",
        ]
        for text in cases:
            with self.subTest(text=text[:80]):
                self.complete.return_value = (text, None)
                with self.assertRaises(GenerationError) as raised:
                    self.generate()
                self.assertNotIn("other private source", str(raised.exception))
                self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_repeated_arrays_cannot_hide_conflicting_edits_or_route_collisions(self):
        base = json.dumps(proposal())[:-1]
        cases = [
            base + ', "files": ' + json.dumps([proposal()["files"][0]]) + "}",
            base
            + ', "files": '
            + json.dumps(
                [{"path": "endpoints/greetings.py", "content": "# conflicting edit"}]
            )
            + "}",
            base
            + ', "files": '
            + json.dumps([{"path": "endpoints/GREETINGS.py", "content": HANDLER}])
            + "}",
            base
            + ', "files": '
            + json.dumps([{"path": "../outside.py", "content": HANDLER}])
            + "}",
            base + ', "endpoints": ' + json.dumps(proposal()["endpoints"]) + "}",
            base
            + ', "endpoints": '
            + json.dumps(
                [
                    {
                        **proposal()["endpoints"][0],
                        "handler": "endpoints/greetings.py:other",
                    }
                ]
            )
            + "}",
        ]
        for text in cases:
            with self.subTest(text=text[:80]):
                self.complete.return_value = (text, None)
                with self.assertRaises(GenerationError):
                    self.generate()
                self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_file_limits_apply_after_combining_all_arrays(self):
        files = proposal()["files"] + [
            {"path": f"endpoints/extra_{index}.py", "content": "# extra"}
            for index in range(19)
        ]
        self.complete.return_value = (
            split_file_proposal({**proposal(), "files": files}),
            None,
        )
        with self.assertRaisesRegex(GenerationError, "at most 20 files"):
            self.generate()
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_json_like_text_inside_source_is_not_rewritten(self):
        plan = proposal()
        content = HANDLER + '\n# {"files": [], "files": ["literal text"]}\n'
        plan["files"][0]["content"] = content
        self.complete.return_value = (split_file_proposal(plan), None)
        result = self.generate()
        directory = drafts.draft_directory(self.project, result["draft"]["id"])
        self.assertEqual((directory / "endpoints/greetings.py").read_text(), content)

    def test_normal_json_has_no_normalization_warning(self):
        self.assertEqual(self.generate()["output_warnings"], [])

    def test_provider_failure_creates_no_draft(self):
        self.complete.side_effect = GenerationError("Groq unavailable")
        with self.assertRaises(GenerationError):
            self.generate()
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_code_change_during_model_request_is_a_conflict(self):
        def concurrent_edit(*args):
            (code_directory(self.project) / "endpoints/hello.py").write_text(
                "# concurrent edit"
            )
            return json.dumps(proposal()), None

        self.complete.side_effect = concurrent_edit
        with self.assertRaisesRegex(ValueError, "changed during generation"):
            self.generate()
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_schema_change_during_model_request_is_a_conflict(self):
        import sqlite3

        def concurrent_edit(*args):
            with sqlite3.connect(self.project.directory / "data.sqlite") as db:
                db.execute("CREATE TABLE new_table(id INTEGER)")
            return json.dumps(proposal()), None

        self.complete.side_effect = concurrent_edit
        with self.assertRaisesRegex(ValueError, "Database definitions changed"):
            self.generate()
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_oversized_context_is_rejected_without_calling_provider(self):
        with (
            patch("reqlica.generation.prompts.MAX_PROMPT_BYTES", 1),
            self.assertRaises(GenerationError) as raised,
        ):
            self.generate()
        self.assertEqual(raised.exception.status_code, 413)
        self.complete.assert_not_called()

    def test_disk_failure_exposes_recoverable_draft_id_not_success(self):
        with (
            patch(
                "reqlica.generation.service.drafts.write_draft_file",
                side_effect=OSError("disk full"),
            ),
            self.assertRaises(GenerationError) as raised,
        ):
            self.generate()
        self.assertEqual(raised.exception.status_code, 500)
        draft_id = raised.exception.detail["draft_id"]
        self.assertIsNone(drafts.inspect_draft(self.project.id, draft_id)["approval"])


class GenerationHTTPTests(GenerationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = self.enterContext(TestClient(create_app()))
        self.route = f"/api/projects/{self.project.id}/generate"

    def test_http_creates_unchecked_draft_and_never_echoes_credentials(self):
        response = self.client.post(
            self.route,
            json={"requirements": "Create a greeting"},
            headers={"X-Reqlica-Groq-Key": "secret-header-key"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("secret-header-key", response.text)
        self.assertEqual(
            self.complete.call_args.args[2].get_secret_value(), "secret-header-key"
        )
        messages = self.complete.call_args.args[0]
        self.assertNotIn("secret-header-key", json.dumps(messages))
        self.assertIsNone(response.json()["draft"]["test"])

    def test_http_input_resource_and_provider_errors(self):
        for body in (
            {},
            {"requirements": " "},
            {"requirements": "x", "history": []},
            {"requirements": "x", "base_url": "http://evil"},
            {"requirements": "x", "model": "unsupported"},
            {"requirements": "x", "max_tokens": 1},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.post(self.route, json=body).status_code, 422
                )
        self.complete.assert_not_called()
        self.assertEqual(
            self.client.post(
                "/api/projects/" + "f" * 32 + "/generate", json={"requirements": "x"}
            ).status_code,
            404,
        )
        for status in (503, 504, 502):
            self.complete.side_effect = GenerationError("Provider unavailable", status)
            response = self.client.post(self.route, json={"requirements": "x"})
            self.assertEqual(response.status_code, status)
            self.assertEqual(
                response.json()["detail"]["message"], "Provider unavailable"
            )

    def test_http_accepts_split_files_and_reports_normalization(self):
        self.complete.return_value = (split_file_proposal(), None)
        response = self.client.post(
            self.route, json={"requirements": "Create a greeting"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(len(result["generated_files"]), 2)
        self.assertTrue(result["output_warnings"])
        self.assertIsNone(result["draft"]["test"])
        self.complete.assert_called_once()


class GroqProviderTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.request = GenerationRequest(requirements="Create an endpoint")
        self.messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "context"},
        ]
        self.requests = []
        self.options = {}
        self.status = 200
        self.exception = None
        self.payload = {
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": self.request.model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(proposal())},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

        def handle(request):
            self.requests.append(request)
            if self.exception:
                raise self.exception("network failure", request=request)
            return httpx.Response(self.status, json=self.payload)

        def factory(**kwargs):
            self.options = kwargs.copy()
            kwargs["http_client"] = httpx.Client(
                transport=httpx.MockTransport(handle), trust_env=False
            )
            return OpenAI(**kwargs)

        self.enterContext(
            patch("reqlica.generation.provider.OpenAI", side_effect=factory)
        )

    def complete(self):
        return provider.complete(
            self.messages, self.request, SecretStr("private-provider-key")
        )

    def test_fixed_origin_strict_json_and_exactly_one_stateless_request(self):
        text, usage = self.complete()
        self.assertEqual(json.loads(text), proposal())
        self.assertEqual(usage["total_tokens"], 30)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(
            str(self.requests[0].url), provider.GROQ_BASE_URL + "/chat/completions"
        )
        self.assertEqual(self.options["max_retries"], 0)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["messages"], self.messages)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertNotIn("tools", body)
        self.assertNotIn("private-provider-key", self.requests[0].content.decode())

    def test_missing_credentials_fail_before_network(self):
        with self.assertRaises(GenerationError) as raised:
            provider.complete(self.messages, self.request)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.requests, [])

    def test_question_mode_uses_its_own_strict_answer_schema_in_one_request(self):
        request = ProjectQuestionRequest(question="Explain this project")
        self.payload["choices"][0]["message"]["content"] = json.dumps(
            {
                "answer": "Explanation",
                "sources": [],
                "limitations": [],
            }
        )
        provider.complete(
            self.messages,
            request,
            SecretStr("private-provider-key"),
            response_model=ProjectAnswer,
        )
        self.assertEqual(len(self.requests), 1)
        body = json.loads(self.requests[0].content)
        schema = body["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertEqual(
            set(schema["schema"]["properties"]), {"answer", "sources", "limitations"}
        )
        self.assertEqual(body["messages"], self.messages)

    def test_existing_groq_environment_name_is_supported(self):
        with patch.dict(os.environ, {"GROQ_API": "legacy-env-key"}):
            provider.complete(self.messages, self.request)
        self.assertEqual(
            self.requests[0].headers["authorization"], "Bearer legacy-env-key"
        )

    def test_provider_errors_are_redacted_and_never_retried(self):
        for status, expected in (
            (401, 502),
            (403, 502),
            (400, 502),
            (429, 503),
            (500, 502),
        ):
            with self.subTest(status=status):
                self.requests.clear()
                self.status = status
                self.payload = {
                    "error": {"message": "private-provider-key and raw provider body"}
                }
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertEqual(raised.exception.status_code, expected)
                self.assertNotIn("private-provider-key", str(raised.exception))
                self.assertEqual(len(self.requests), 1)

    def test_timeout_and_connection_errors(self):
        for kind, expected in ((httpx.ReadTimeout, 504), (httpx.ConnectError, 502)):
            with self.subTest(kind=kind):
                self.requests.clear()
                self.exception = kind
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertEqual(raised.exception.status_code, expected)
                self.assertEqual(len(self.requests), 1)

    def test_bad_requests_have_actionable_reasons_without_raw_provider_content(self):
        cases = [
            ({"code": "json_validate_failed"}, "required JSON schema"),
            ({"code": "json_generation_failed"}, "required JSON schema"),
            (
                {"message": "Generated JSON does not match the expected schema"},
                "required JSON schema",
            ),
            ({"message": "Failed to generate JSON"}, "required JSON schema"),
            ({"code": "context_length_exceeded"}, "context limit"),
            ({"message": "Maximum context window exceeded"}, "context limit"),
            ({"message": "Invalid JSON schema"}, "provider/schema compatibility"),
            ({"param": "max_completion_tokens"}, "Lower Max output tokens"),
            (
                {"message": "max_tokens must be below the model limit"},
                "Lower Max output tokens",
            ),
            ({"code": "model_not_found"}, "selected model"),
            ({"param": "reasoning_effort"}, "generation setting"),
            ({"param": "response_format"}, "generation setting"),
            ({"code": "unrecognized"}, "not return a recognized failure reason"),
        ]
        self.status = 400
        for detail, expected in cases:
            with self.subTest(detail=detail):
                self.requests.clear()
                detail = {
                    **detail,
                    "message": detail.get("message", "")
                    + " private-provider-key private-project-source",
                    "failed_generation": "private generated endpoint source",
                }
                self.payload = {"error": detail}
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertEqual(raised.exception.status_code, 502)
                self.assertIn(expected, str(raised.exception))
                self.assertIn("No draft was saved", str(raised.exception))
                self.assertNotIn("private", json.dumps(raised.exception.detail))
                self.assertEqual(len(self.requests), 1)

    def test_malformed_bad_request_diagnostics_do_not_break_error_handling(self):
        self.status = 400
        for payload in (
            None,
            "private raw response",
            [],
            {"error": "private raw response"},
            {"error": {"message": [], "code": {}, "param": {}}},
        ):
            with self.subTest(payload=payload):
                self.requests.clear()
                self.payload = payload
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertIn("HTTP 400", str(raised.exception))
                self.assertNotIn("private", str(raised.exception))
                self.assertEqual(len(self.requests), 1)

    def test_truncated_empty_and_refused_responses_are_not_accepted(self):
        for kind in ("truncated", "empty", "refused", "oversized"):
            with self.subTest(kind=kind):
                choice = self.payload["choices"][0]
                choice["finish_reason"] = "length" if kind == "truncated" else "stop"
                choice["message"]["content"] = (
                    "x" * (provider.MAX_RESPONSE_BYTES + 1)
                    if kind == "oversized"
                    else ""
                )
                choice["message"]["refusal"] = "No" if kind == "refused" else None
                with self.assertRaises(GenerationError):
                    self.complete()
