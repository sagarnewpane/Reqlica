"""Read-only design questions and revision-bound user decisions before generation."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from reqlica.console.app import create_app
from reqlica.generation.clarification import review_requirements
from reqlica.generation.errors import GenerationError
from reqlica.generation.prompts import build_messages
from reqlica.generation.schemas import (
    ClarificationDecision,
    ClarificationRequest,
    ClarificationReview,
    ClarificationSubmission,
    GenerationRequest,
)
from reqlica.generation.service import endpoint_source, generate_endpoints
from reqlica.projects import drafts
from reqlica.projects.environment import change_environment, get_environment
from reqlica.projects.store import create_project
from reqlica.projects.versions import code_directory, content_digest

QUESTIONS = [
    {
        "id": "user_fields",
        "kind": "field",
        "question": "Which user fields should signup accept and persist?",
        "options": ["Email and password only", "Username, email and password"],
    },
    {
        "id": "jwt_config",
        "kind": "environment",
        "question": "Use JWT_SECRET from Project environment and return 503 if missing?",
        "options": [
            "Use JWT_SECRET; missing configuration returns 503",
            "Use REQLICA_JWT_SECRET; fail startup if missing",
        ],
    },
]


class ClarificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")})
        )
        self.project = create_project(self.root / "project", "Clarification example")
        self.request = ClarificationRequest(requirements="Build signup and JWT login")
        self.complete = self.enterContext(
            patch("reqlica.generation.clarification.provider.complete")
        )
        self.complete.return_value = (
            json.dumps(
                {
                    "summary": "Confirm fields and JWT configuration",
                    "questions": QUESTIONS,
                }
            ),
            None,
        )

    def review(self):
        return review_requirements(self.project.id, self.request)

    def submission(self, review):
        return ClarificationSubmission(
            context_fingerprint=review["context_fingerprint"],
            decisions=[
                ClarificationDecision(question=q, answer=q["options"][0])
                for q in review["questions"]
            ],
        )

    def test_review_asks_fields_and_env_policy_without_writing_or_sending_values(self):
        secret = "private-review-key-not-model-input"
        change_environment(
            self.project.id,
            "JWT_SECRET",
            get_environment(self.project.id)["revision"],
            SecretStr(secret),
        )
        before = content_digest(code_directory(self.project))
        database = (self.project.directory / "data.sqlite").read_bytes()
        result = self.review()
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["questions"], QUESTIONS)
        self.assertTrue(result["read_only"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])
        self.assertEqual(content_digest(code_directory(self.project)), before)
        self.assertEqual(
            (self.project.directory / "data.sqlite").read_bytes(), database
        )
        messages = self.complete.call_args.args[0]
        self.assertIn("JWT_SECRET", json.dumps(messages))
        self.assertNotIn(secret, json.dumps(messages))
        self.assertIn("Never ask the user to", messages[0]["content"])
        self.assertIn('"options":["SQLite","Stateless"]', messages[0]["content"])
        self.assertIn('"options":[]', messages[0]["content"])
        self.assertIn('["SQLite", "Stateless", []] is INVALID', messages[0]["content"])
        self.assertIs(
            self.complete.call_args.kwargs["response_model"], ClarificationReview
        )
        self.complete.assert_called_once()

    def test_explicit_spec_can_be_ready_without_redundant_questions(self):
        self.complete.return_value = (
            json.dumps(
                {"summary": "All fields and behavior are explicit", "questions": []}
            ),
            None,
        )
        result = self.review()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.submission(result).decisions, [])

    def test_provider_schema_exposes_suggested_answer_constraints(self):
        schema = ClarificationReview.model_json_schema()
        option = schema["$defs"]["ClarificationQuestion"]["properties"]["options"][
            "items"
        ]
        self.assertEqual(option["minLength"], 1)
        self.assertEqual(option["maxLength"], 512)
        self.assertEqual(option["pattern"], r"\S")
        for value in ("", "   ", "\n\t", "x" * 513):
            with (
                self.subTest(value_length=len(value)),
                self.assertRaises(ValidationError),
            ):
                ClarificationReview.model_validate(
                    {
                        "summary": "Configuration choices",
                        "questions": [{**QUESTIONS[0], "options": [value]}],
                    }
                )

    def test_openapi_review_supports_free_text_without_empty_options(self):
        self.request = ClarificationRequest(
            requirements=(Path(__file__).parent / "auth-jwt.openapi.yaml").read_text(),
            model="openai/gpt-oss-120b",
        )
        self.complete.return_value = (
            json.dumps(
                {
                    "summary": "Choose the JWT variable name",
                    "questions": [{**QUESTIONS[1], "options": []}],
                }
            ),
            None,
        )
        result = self.review()
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["questions"][0]["options"], [])
        messages = self.complete.call_args.args[0]
        self.assertEqual(
            json.loads(messages[1]["content"])["requirements"],
            self.request.requirements,
        )
        self.assertIn("NEVER put an array, object, null or blank string INSIDE options", messages[0]["content"])
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_empty_option_failure_has_safe_actionable_http_diagnostics(self):
        private_text = "untrusted-sensitive-model-output"
        self.complete.return_value = (
            json.dumps(
                {
                    "summary": private_text,
                    "questions": [{**QUESTIONS[0], "options": [private_text, ""]}],
                }
            ),
            None,
        )
        with TestClient(create_app()) as client:
            response = client.post(
                f"/api/projects/{self.project.id}/clarify",
                json=self.request.model_dump(),
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("suggested answers", response.text)
        self.assertIn("nonblank strings", response.text)
        self.assertIn("No draft was created", response.text)
        self.assertNotIn(private_text, response.text)
        self.complete.assert_called_once()
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_validation_diagnostics_never_echo_untrusted_field_names(self):
        private_text = "untrusted-sensitive-field-name"
        self.complete.return_value = (
            json.dumps(
                {
                    "summary": "Configuration choices",
                    "questions": [{**QUESTIONS[0], private_text: private_text}],
                }
            ),
            None,
        )
        with self.assertRaises(GenerationError) as raised:
            self.review()
        self.assertNotIn(private_text, str(raised.exception))
        self.assertIn("required question schema", str(raised.exception))

    def test_generation_receives_explicit_user_decisions(self):
        result = self.review()
        request = GenerationRequest(
            requirements=self.request.requirements,
            clarification=self.submission(result),
        )
        _, messages = build_messages(self.project.id, request)
        decisions = json.loads(messages[1]["content"])["clarification_decisions"]
        self.assertEqual(decisions[0]["answer"], "Email and password only")
        self.assertEqual(decisions[1]["question"]["kind"], "environment")
        self.assertNotIn("options", decisions[0]["question"])
        self.assertEqual(
            request.clarification.decisions[0].question.options,
            QUESTIONS[0]["options"],
        )
        incomplete = request.clarification.model_copy(
            update={"decisions": request.clarification.decisions[:1]}
        )
        with self.assertRaisesRegex(ValueError, "review clarification"):
            build_messages(
                self.project.id,
                request.model_copy(update={"clarification": incomplete}),
            )

    def test_changed_requirements_source_schema_or_variable_names_require_fresh_review(
        self,
    ):
        for change in ("requirements", "source", "database", "environment"):
            with self.subTest(change=change):
                submission = self.submission(self.review())
                self.complete.reset_mock()
                request = GenerationRequest(
                    requirements=self.request.requirements, clarification=submission
                )
                if change == "requirements":
                    request = request.model_copy(
                        update={"requirements": "Different fields"}
                    )
                elif change == "source":
                    (code_directory(self.project) / "models").mkdir(exist_ok=True)
                    (code_directory(self.project) / "models/new.py").write_text(
                        "# new code"
                    )
                elif change == "database":
                    with sqlite3.connect(self.project.directory / "data.sqlite") as db:
                        db.execute("CREATE TABLE new_table(id INTEGER)")
                else:
                    change_environment(
                        self.project.id,
                        "NEW_NAME",
                        get_environment(self.project.id)["revision"],
                        SecretStr("test-only"),
                    )
                with self.assertRaisesRegex(ValueError, "review clarification"):
                    generate_endpoints(self.project.id, request)
                self.complete.assert_not_called()

    def test_endpoint_review_preserves_selected_route_and_checks_revision(self):
        source = endpoint_source(self.project.id, "GET", "/hello")
        request = ClarificationRequest(
            requirements="Add an optional greeting field",
            endpoint=source["endpoint"],
            expected_revision=source["revision"],
        )
        result = review_requirements(self.project.id, request)
        payload = json.loads(self.complete.call_args.args[0][1]["content"])
        self.assertEqual(payload["edit_target"], source["endpoint"])
        self.assertEqual(result["status"], "needs_clarification")
        self.complete.reset_mock()
        request = request.model_copy(update={"expected_revision": "a" * 64})
        with self.assertRaisesRegex(ValueError, "Published code changed"):
            review_requirements(self.project.id, request)
        self.complete.assert_not_called()

    def test_followup_reviews_preserve_prior_decisions(self):
        first = self.review()
        answered = self.submission(first)
        self.complete.return_value = (
            json.dumps(
                {
                    "summary": "One remaining decision",
                    "questions": [
                        {
                            "id": "token_lifetime",
                            "kind": "behavior",
                            "question": "Token lifetime?",
                            "options": ["15 minutes", "1 hour"],
                        }
                    ],
                }
            ),
            None,
        )
        followup = review_requirements(
            self.project.id, self.request.model_copy(update={"clarification": answered})
        )
        self.assertEqual(len(followup["prior_decisions"]), 2)
        submission = ClarificationSubmission(
            context_fingerprint=followup["context_fingerprint"],
            decisions=followup["prior_decisions"]
            + [{"question": followup["questions"][0], "answer": "15 minutes"}],
        )
        _, messages = build_messages(
            self.project.id,
            GenerationRequest(
                requirements=self.request.requirements, clarification=submission
            ),
        )
        self.assertEqual(
            len(json.loads(messages[1]["content"])["clarification_decisions"]), 3
        )

    def test_invalid_reviews_are_rejected_not_retried(self):
        for data in (
            {"summary": "", "questions": []},
            {"summary": "Questions", "questions": QUESTIONS * 2},
            {
                "summary": "Questions",
                "questions": [{**QUESTIONS[0], "options": ["x" * 513]}],
            },
            {"summary": "Questions", "questions": QUESTIONS, "files": []},
        ):
            with self.subTest(data=str(data)[:80]), self.assertRaises(GenerationError):
                self.complete.return_value = (json.dumps(data), None)
                self.review()
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_http_review_and_bounded_answer_validation(self):
        with TestClient(create_app()) as client:
            response = client.post(
                f"/api/projects/{self.project.id}/clarify",
                json=self.request.model_dump(),
                headers={"X-Reqlica-Groq-Key": "private-provider-key"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("private-provider-key", response.text)
            self.assertEqual(
                self.complete.call_args.args[2].get_secret_value(),
                "private-provider-key",
            )
            body = GenerationRequest(
                requirements=self.request.requirements,
                clarification=self.submission(response.json()),
            ).model_dump()
            body["clarification"]["decisions"][0]["answer"] = " "
            rejected = client.post(
                f"/api/projects/{self.project.id}/generate", json=body
            )
            self.assertEqual(rejected.status_code, 422)
