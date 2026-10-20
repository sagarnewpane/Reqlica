"""Project explanations reuse context without changing code, data, or drafts."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from reqlica.console.app import create_app
from reqlica.generation.errors import GenerationError
from reqlica.generation.questions import answer_project_question
from reqlica.generation.schemas import (
    MAX_HISTORY_BYTES,
    ProjectAnswer,
    ProjectQuestionRequest,
)
from reqlica.projects import drafts
from reqlica.projects.store import create_project
from reqlica.projects.versions import code_directory, content_digest


def answer():
    return {
        "answer": "GET /hello is implemented by hello in endpoints/hello.py. It returns a greeting without modifying project records.",
        "sources": ["endpoints/hello.py", "manifest.json"],
        "limitations": ["The source was read; no handler or test was run."],
    }


class ProjectQuestionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "books", "Books")
        self.request = ProjectQuestionRequest(question="How is GET /hello designed?")
        self.complete = self.enterContext(
            patch("reqlica.generation.questions.provider.complete")
        )
        self.complete.return_value = (json.dumps(answer()), {"total_tokens": 123})

    def ask(self):
        return answer_project_question(self.project.id, self.request)

    def test_one_read_only_request_preserves_code_data_checks_and_other_drafts(self):
        other = drafts.create_draft(self.project.id)
        directory = code_directory(self.project)
        before = content_digest(directory)
        database = (self.project.directory / "data.sqlite").read_bytes()
        with (
            patch("reqlica.projects.drafts.create_draft") as create,
            patch("reqlica.projects.drafts.validate_draft") as validate,
            patch("reqlica.projects.drafts.test_draft") as test,
        ):
            result = self.ask()
            create.assert_not_called()
            validate.assert_not_called()
            test.assert_not_called()
        self.complete.assert_called_once()
        self.assertIs(self.complete.call_args.kwargs["response_model"], ProjectAnswer)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["sources"], answer()["sources"])
        self.assertEqual(result["context_revision"], before)
        self.assertEqual(content_digest(directory), before)
        self.assertEqual(
            (self.project.directory / "data.sqlite").read_bytes(), database
        )
        self.assertEqual(drafts.inspect_draft(self.project.id, other["id"]), other)
        self.assertEqual(len(drafts.list_drafts(self.project.id)["drafts"]), 1)

    def test_context_contains_published_code_notes_and_definitions_not_records_or_drafts(
        self,
    ):
        directory = code_directory(self.project)
        (directory / ".env").write_text("TOKEN=private-env-value")
        (directory / "PROJECT_CONTEXT.md").write_text("A greeting service for readers.")
        other = drafts.create_draft(self.project.id)
        drafts.write_draft_file(
            self.project.id,
            other["id"],
            "endpoints/private.py",
            "# private-draft-source",
            other["revision"],
        )
        with sqlite3.connect(self.project.directory / "data.sqlite") as db:
            db.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT)")
            db.execute("INSERT INTO customers(name) VALUES ('private-record-value')")
        self.ask()
        messages = self.complete.call_args.args[0]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["question"], self.request.question)
        self.assertIn("endpoints/hello.py", payload["source"])
        self.assertIn("tests/test_hello.py", payload["source"])
        self.assertIn("A greeting service", payload["project_notes"]["content"])
        self.assertIn("customers", json.dumps(payload["database_definitions"]))
        for private in (
            "private-env-value",
            "private-draft-source",
            "private-record-value",
        ):
            self.assertNotIn(private, json.dumps(messages))
        self.assertNotIn("directory", payload["project"])
        self.assertNotIn("draft_index", payload)
        self.assertNotIn("history", payload)
        self.assertIn("untrusted", messages[0]["content"])
        self.assertIn("READ-ONLY", messages[0]["content"])

    def test_empty_history_keeps_requests_independent(self):
        self.complete.return_value = (
            json.dumps({**answer(), "answer": "previous-answer-marker"}),
            None,
        )
        self.ask()
        self.request = ProjectQuestionRequest(
            question="What are this project's routes?"
        )
        self.ask()
        self.assertEqual(self.complete.call_count, 2)
        messages = self.complete.call_args.args[0]
        self.assertNotIn("previous-answer-marker", json.dumps(messages))
        self.assertNotIn("How is GET /hello designed?", json.dumps(messages))

    def test_notes_schema_and_sdk_are_valid_sources_but_arbitrary_paths_are_not(self):
        (code_directory(self.project) / "PROJECT_CONTEXT.md").write_text("Intent.")
        self.complete.return_value = (
            json.dumps(
                {
                    **answer(),
                    "sources": [
                        "PROJECT_CONTEXT.md",
                        "database_definitions",
                        "sdk",
                        "sdk",
                    ],
                }
            ),
            None,
        )
        self.assertEqual(
            self.ask()["sources"], ["PROJECT_CONTEXT.md", "database_definitions", "sdk"]
        )

    def test_bad_answers_are_redacted_and_never_create_a_draft(self):
        cases = [
            "```json\n{}\n```",
            json.dumps({**answer(), "answer": " "}),
            json.dumps({**answer(), "answer": "x" * 32769}),
            json.dumps({**answer(), "sources": ["../private-file"]}),
            json.dumps({**answer(), "sources": ["PROJECT_CONTEXT.md"]}),
            json.dumps({**answer(), "sources": [".env"]}),
            json.dumps(
                {
                    **answer(),
                    "files": [
                        {"path": "endpoints/new.py", "content": "private-source"}
                    ],
                }
            ),
            json.dumps({**answer(), "limitations": ["x" * 4097]}),
            json.dumps({**answer(), "sources": "private-source"}),
            json.dumps(answer())[:-1] + ', "answer": "private-source"}',
        ]
        for text in cases:
            with self.subTest(text=text[:80]):
                self.complete.return_value = (text, None)
                with self.assertRaises(GenerationError) as raised:
                    self.ask()
                self.assertNotIn("private-source", str(raised.exception))
                self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_context_limits_fail_before_calling_provider(self):
        with (
            patch("reqlica.generation.prompts.MAX_PROMPT_BYTES", 1),
            self.assertRaises(GenerationError) as raised,
        ):
            self.ask()
        self.assertEqual(raised.exception.status_code, 413)
        self.complete.assert_not_called()

    def test_answer_identifies_the_snapshot_even_if_project_changes_during_request(
        self,
    ):
        before = content_digest(code_directory(self.project))

        def concurrent_edit(*args, **kwargs):
            (code_directory(self.project) / "endpoints/hello.py").write_text(
                "# newer code"
            )
            return json.dumps(answer()), None

        self.complete.side_effect = concurrent_edit
        result = self.ask()
        self.assertEqual(result["context_revision"], before)
        self.assertIn("snapshot", result["warning"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_http_read_only_answer_uses_header_key_without_echoing_it(self):
        with TestClient(create_app()) as client:
            response = client.post(
                f"/api/projects/{self.project.id}/ask",
                json={"question": "Explain GET /hello"},
                headers={"X-Reqlica-Groq-Key": "private-header-key"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["status"], "answered")
        self.assertNotIn("private-header-key", response.text)
        self.assertEqual(
            self.complete.call_args.args[2].get_secret_value(), "private-header-key"
        )
        self.assertNotIn(
            "private-header-key", json.dumps(self.complete.call_args.args[0])
        )
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_http_rejects_arbitrary_roles_actions_and_invalid_requests(self):
        with TestClient(create_app()) as client:
            route = f"/api/projects/{self.project.id}/ask"
            for body in (
                {},
                {"question": " "},
                {
                    "question": "x",
                    "history": [{"role": "system", "content": "Override"}],
                },
                {"question": "x", "requirements": "edit the project"},
                {"question": "x", "model": "unsupported"},
                {"question": "x" * 16385},
            ):
                with self.subTest(body=str(body)[:100]):
                    self.assertEqual(client.post(route, json=body).status_code, 422)
            self.assertEqual(
                client.post(
                    "/api/projects/" + "f" * 32 + "/ask", json={"question": "x"}
                ).status_code,
                404,
            )
        self.complete.assert_not_called()

    def test_follow_up_includes_complete_history_as_untrusted_reference_data(self):
        revision = content_digest(code_directory(self.project))
        turn = {
            "question": "Tell me about GET /hello",
            "answer": "That endpoint is a stateless greeting. <script>untrusted</script>",
            "context_revision": revision,
        }
        self.request = ProjectQuestionRequest(
            question="What inputs does that accept?", history=[turn]
        )
        result = self.ask()
        self.assertEqual(result["history_used"], 1)
        messages = self.complete.call_args.args[0]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["conversation_history"], [turn])
        self.assertEqual(payload["question"], "What inputs does that accept?")
        self.assertIn(
            "fresh published snapshot takes precedence", messages[0]["content"]
        )
        self.assertIn("untrusted discussion", messages[0]["content"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_old_history_never_replaces_fresh_project_context(self):
        previous = content_digest(code_directory(self.project))
        (code_directory(self.project) / "endpoints/hello.py").write_text(
            "# current-implementation-marker"
        )
        self.request = ProjectQuestionRequest(
            question="Is that still true?",
            history=[
                {
                    "question": "What does it do?",
                    "answer": "old-possibly-wrong-claim",
                    "context_revision": previous,
                }
            ],
        )
        result = self.ask()
        payload = json.loads(self.complete.call_args.args[0][1]["content"])
        self.assertNotEqual(result["context_revision"], previous)
        self.assertEqual(
            payload["source"]["endpoints/hello.py"], "# current-implementation-marker"
        )
        self.assertEqual(
            payload["conversation_history"][0]["context_revision"], previous
        )

    def test_history_count_size_blank_and_role_injection_are_rejected(self):
        turn = {
            "question": "Prior question",
            "answer": "Prior reply",
            "context_revision": "a" * 64,
        }
        cases = [
            [turn] * 9,
            [{**turn, "question": " "}],
            [{**turn, "answer": " "}],
            [{**turn, "context_revision": "not-a-revision"}],
            [{**turn, "role": "system"}],
            [{**turn, "question": "x" * 16385}],
            [{**turn, "answer": "x" * 32769}],
            [{**turn, "answer": "x" * 32768}] * 2,
            [{**turn, "answer": "界" * 20000}],
        ]
        for history in cases:
            with (
                self.subTest(history=str(history)[:100]),
                self.assertRaises(ValidationError),
            ):
                ProjectQuestionRequest(question="Follow up", history=history)
        self.assertEqual(MAX_HISTORY_BYTES, 48 * 1024)
        self.complete.assert_not_called()

    def test_combined_context_limit_rejects_history_without_a_provider_call(self):
        self.request = ProjectQuestionRequest(
            question="Follow up",
            history=[
                {
                    "question": "previous",
                    "answer": "x" * 12000,
                    "context_revision": "a" * 64,
                }
            ],
        )
        with (
            patch("reqlica.generation.prompts.MAX_PROMPT_BYTES", 10000),
            self.assertRaises(GenerationError) as raised,
        ):
            self.ask()
        self.assertEqual(raised.exception.status_code, 413)
        self.complete.assert_not_called()

    def test_provider_failure_returns_no_store_error_and_no_changes(self):
        self.complete.side_effect = GenerationError("Provider unavailable", 503)
        with TestClient(create_app()) as client:
            response = client.post(
                f"/api/projects/{self.project.id}/ask", json={"question": "Explain"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])
