"""Product-level recovery of failed drafts, without weakening tests or publishing."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr
from reqlica.console.app import create_app
from reqlica.generation.errors import GenerationError
from reqlica.generation.repair import _compact_checks, _compact_decisions, repair_draft
from reqlica.generation.schemas import RepairPlan, RepairRequest
from reqlica.projects import drafts
from reqlica.projects.environment import change_environment, get_environment
from reqlica.projects.store import create_project
from reqlica.projects.versions import atomic_write_json, code_directory, content_digest


class RepairContextTests(unittest.TestCase):
    def test_duplicate_environment_failures_keep_evidence_and_distinct_causes(self):
        detail = 'Traceback:\n  File "endpoints/example.py", line 12\n    return missing\n' + "framework stack\n" * 80 + "NameError: missing\n"
        failures = [
            {"test": name, "kind": "error", "output": detail}
            for name in ("test_example", "[UTC] test_example", "[Asia/Kathmandu] test_example")
        ] + [{"test": "[UTC] test_example", "kind": "error", "output": "ValueError: different cause"}]
        state = {"test": {"status": "failed", "failures": failures, "output": "redundant transcript"}}
        before = json.dumps(state)
        compact = _compact_checks(state)["test"]
        self.assertEqual(len(compact["failures"]), 2)
        first = compact["failures"][0]
        self.assertEqual(first["environments"], ["default", "UTC", "Asia/Kathmandu"])
        self.assertIn('endpoints/example.py', first["output"])
        self.assertIn("NameError: missing", first["output"])
        self.assertLess(len(json.dumps(compact)), len(before))
        self.assertEqual(json.dumps(state), before)

    def test_shared_cause_keeps_all_affected_tests(self):
        failures = [{"test": name, "kind": "error", "output": "TypeError: datetime"}
                    for name in ("test_register", "test_login", "[UTC] test_login")]
        compact = _compact_checks({"test": {"failures": failures}})
        evidence = compact["test"]["failures"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["affected_tests"], ["test_register", "test_login"])
        self.assertEqual(evidence[0]["environments"], ["default", "UTC"])

    def test_decisions_keep_intent_without_unused_options(self):
        original = {"context_fingerprint": "abc", "decisions": [
            {"question": {"question": "Token lifetime?", "options": ["1h", "24h"]},
             "answer": "1h"}]}
        self.assertEqual(_compact_decisions(original),
                         [{"question": "Token lifetime?", "answer": "1h"}])
        self.assertIn("options", original["decisions"][0]["question"])


BROKEN = """from fastapi import Depends, HTTPException
def hello():
    request = Depends()
    return {"value": request.headers.get("x-value")}
def login():
    raise HTTPException(401, detail={"code": "INVALID_CREDENTIALS"})
"""
FIXED = """from fastapi import Request
from fastapi.responses import JSONResponse
def hello(request: Request) -> dict[str, str]:
    return {"value": request.headers.get("x-value", "")}
def login():
    return JSONResponse(status_code=401, content={"code": "INVALID_CREDENTIALS"})
"""
TESTS = """import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from endpoints.hello import hello, login
class OriginalTests(unittest.TestCase):
    def test_header_injection(self):
        app = FastAPI()
        app.add_api_route("/hello", hello)
        with TestClient(app) as client:
            response = client.get("/hello", headers={"x-value": "expected"})
        self.assertEqual(response.json(), {"value": "expected"})
    def test_error_contract(self):
        app = FastAPI()
        app.add_api_route("/login", login)
        with TestClient(app) as client:
            response = client.get("/login")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"code": "INVALID_CREDENTIALS"})
"""


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")})
        )
        self.project = create_project(self.root / "project")
        self.draft = drafts.create_draft(self.project.id)
        for path, code in (
            ("endpoints/hello.py", BROKEN),
            ("tests/test_hello.py", TESTS),
        ):
            self.draft = drafts.write_draft_file(
                self.project.id, self.draft["id"], path, code, self.draft["revision"]
            )
        self.failure = drafts.test_draft(
            self.project.id, self.draft["id"], self.draft["revision"]
        )
        self.assertEqual(self.failure["status"], "failed")
        self.directory = drafts.draft_directory(self.project, self.draft["id"])
        self.request = RepairRequest(
            expected_revision=self.draft["revision"],
            confirm=True,
            requirements="GET /hello reads x-value. Errors must return top-level code, not detail. Preserve the API contract.",
        )
        self.complete = self.enterContext(
            patch("reqlica.generation.repair.provider.complete")
        )
        self.complete.return_value = (json.dumps(self.plan()), {"total_tokens": 100})

    def plan(self, content=FIXED):
        return {
            "summary": "Correct request injection and the documented error envelope",
            "files": [{"path": "endpoints/hello.py", "content": content}],
            "blockers": [],
        }

    def repair(self, request=None):
        return repair_draft(self.project.id, self.draft["id"], request or self.request)

    def test_system_repairs_real_fastapi_failures_and_checks_without_touching_tests_or_live_data(
        self,
    ):
        published = content_digest(code_directory(self.project))
        database = (self.project.directory / "data.sqlite").read_bytes()
        result = self.repair()
        self.assertEqual(result["status"], "repaired", result)
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertEqual(result["test"]["tests_passed"], 2)
        self.assertEqual(result["draft"]["id"], self.draft["id"])
        self.assertEqual((self.directory / "tests/test_hello.py").read_text(), TESTS)
        self.assertIsNone(result["draft"]["approval"])

        rerun = drafts.test_draft(
            self.project.id, self.draft["id"], result["draft"]["revision"]
        )
        self.assertEqual(rerun["status"], "passed")
        self.assertEqual(content_digest(code_directory(self.project)), published)
        self.assertEqual(
            (self.project.directory / "data.sqlite").read_bytes(), database
        )
        backup = self.project.directory / result["draft"]["repair"]["backup"]
        self.assertEqual((backup / "endpoints/hello.py").read_text(), BROKEN)
        self.assertIn("before/endpoints/hello.py", result["diff"])
        self.assertEqual(
            result["draft"]["generation"]["requirements"], self.request.requirements
        )
        payload = json.loads(self.complete.call_args.args[0][1]["content"])
        self.assertEqual(payload["source"]["tests/test_hello.py"], TESTS)
        self.assertEqual(payload["requirements"], self.request.requirements)
        details = payload["failed_checks"]["test"]["failures"]
        self.assertIn("AttributeError", json.dumps(details))
        self.assertIn("AssertionError", json.dumps(details))
        self.assertEqual(len(details), 2)
        self.assertIs(self.complete.call_args.kwargs["response_model"], RepairPlan)
        self.complete.assert_called_once()

    def test_requires_consent_current_failure_and_original_spec_before_provider_call(
        self,
    ):
        for request in (
            self.request.model_copy(update={"confirm": False}),
            self.request.model_copy(update={"requirements": None}),
            self.request.model_copy(update={"expected_revision": "a" * 64}),
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.repair(request)
        self.complete.assert_not_called()

    def test_repair_with_undefined_name_preserves_original_draft(self):
        before = content_digest(self.directory)
        broken = FIXED.replace(
            'return {"value":', 'return missing_response or {"value":'
        )
        self.complete.return_value = (
            json.dumps(self.plan(broken)),
            None,
        )
        with self.assertRaisesRegex(GenerationError, "missing_response"):
            self.repair()
        self.assertEqual(content_digest(self.directory), before)

    def test_saved_contract_cannot_be_replaced(self):
        state = drafts._state(self.project, self.draft["id"])
        state["generation"] = {
            "requirements": "Original protected contract",
            "clarification": None,
        }
        atomic_write_json(drafts._state_path(self.project, self.draft["id"]), state)
        with self.assertRaisesRegex(ValueError, "cannot replace"):
            self.repair()
        self.complete.assert_not_called()

    def test_no_checks_or_passed_draft_cannot_be_repaired(self):
        draft = drafts.create_draft(self.project.id)
        with self.assertRaisesRegex(ValueError, "current failure"):
            repair_draft(
                self.project.id,
                draft["id"],
                self.request.model_copy(
                    update={"expected_revision": draft["revision"]}
                ),
            )
        self.complete.assert_not_called()

    def test_protected_files_invalid_source_and_duplicate_paths_are_rejected_before_writes(
        self,
    ):
        for path in (
            "tests/test_hello.py",
            "manifest.json",
            "database/schema.json",
            "../outside.py",
            "PROJECT_CONTEXT.md",
            "requirements.txt",
            "models/versions/unsafe.py",
            "endpoints/HELLO.py",
        ):
            plan = self.plan()
            plan["files"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(GenerationError):
                self.complete.return_value = (json.dumps(plan), None)
                self.repair()
        for plan in (
            self.plan("def invalid(:"),
            {**self.plan(), "files": self.plan()["files"] * 2},
        ):
            with self.assertRaises(GenerationError):
                self.complete.return_value = (json.dumps(plan), None)
                self.repair()
        self.assertEqual((self.directory / "endpoints/hello.py").read_text(), BROKEN)
        self.assertEqual((self.directory / "tests/test_hello.py").read_text(), TESTS)

    def test_dependency_blockers_do_not_install_or_save_code(self):
        self.complete.return_value = (
            json.dumps(self.plan("import not_an_approved_dependency\n" + FIXED)),
            None,
        )
        result = self.repair()
        self.assertEqual(result["status"], "needs_information")
        self.assertEqual(result["changed_files"], [])
        self.assertEqual((self.directory / "endpoints/hello.py").read_text(), BROKEN)

    def test_model_blockers_stop_without_changes_or_tests(self):
        self.complete.return_value = (
            json.dumps(
                {
                    "summary": "Need a JWT configuration decision",
                    "files": [],
                    "blockers": [
                        "Choose the intended secret variable name in settings; do not paste its value."
                    ],
                }
            ),
            None,
        )
        with patch(
            "reqlica.projects.drafts.validate_draft",
            side_effect=AssertionError("must not execute"),
        ):
            result = self.repair()
        self.assertEqual(result["status"], "needs_information")
        self.assertEqual(result["changed_files"], [])

    def test_concurrent_user_edits_are_never_overwritten(self):
        def complete(*args, **kwargs):
            drafts.write_draft_file(
                self.project.id,
                self.draft["id"],
                "models/user_work.py",
                "# concurrent user change\n",
                self.draft["revision"],
            )
            return json.dumps(self.plan()), None

        self.complete.side_effect = complete
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            self.repair()
        self.assertEqual((self.directory / "endpoints/hello.py").read_text(), BROKEN)
        self.assertTrue((self.directory / "models/user_work.py").exists())

    def test_environment_changes_during_provider_call_stop_before_writing(self):
        def complete(*args, **kwargs):
            change_environment(
                self.project.id,
                "JWT_SECRET",
                get_environment(self.project.id)["revision"],
                SecretStr("changed-value"),
            )
            return json.dumps(self.plan()), None

        self.complete.side_effect = complete
        with self.assertRaisesRegex(ValueError, "environment changed"):
            self.repair()
        self.assertEqual((self.directory / "endpoints/hello.py").read_text(), BROKEN)

    def test_failed_candidate_is_saved_but_not_approved_and_one_call_does_not_loop(
        self,
    ):
        self.complete.return_value = (
            json.dumps(self.plan(BROKEN + "\n# Attempt made\n")),
            None,
        )
        result = self.repair()
        self.assertEqual(result["status"], "checks_failed")
        self.assertIsNone(result["draft"]["approval"])
        self.complete.assert_called_once()

    def test_skipping_failed_tests_cannot_turn_repair_green(self):
        cheating = (
            FIXED
            + '\nimport unittest\ndef _setup(self):\n    if self._testMethodName == "test_error_contract": self.skipTest("skip failure")\nunittest.TestCase.setUp = _setup\n'
        )
        self.complete.return_value = (json.dumps(self.plan(cheating)), None)
        result = self.repair()
        self.assertEqual(result["status"], "checks_failed", result)
        self.assertEqual(result["test"]["status"], "failed")
        self.assertEqual(result["draft"]["repair"]["required_passes"], 2)
        self.assertIsNone(result["draft"]["approval"])
        rerun = drafts.test_draft(
            self.project.id, self.draft["id"], result["draft"]["revision"]
        )
        self.assertEqual(rerun["status"], "failed")

    def test_provider_errors_are_not_retried_and_do_not_modify_the_draft(self):
        self.complete.side_effect = GenerationError("Provider unavailable", 503)
        with self.assertRaises(GenerationError):
            self.repair()
        self.complete.assert_called_once()
        self.assertEqual(content_digest(self.directory), self.draft["revision"])

    def test_failure_logs_and_context_redact_configured_values_before_sharing(self):
        secret = "private-répair-secret-that-must-not-reach-the-model"
        change_environment(
            self.project.id,
            "JWT_SECRET",
            get_environment(self.project.id)["revision"],
            SecretStr(secret),
        )
        drafts.test_draft(self.project.id, self.draft["id"], self.draft["revision"])
        state = drafts._state(self.project, self.draft["id"])
        state["test"]["failures"][0]["output"] += secret
        atomic_write_json(drafts._state_path(self.project, self.draft["id"]), state)
        self.repair()
        messages = self.complete.call_args.args[0]
        self.assertNotIn(secret, json.dumps(messages, ensure_ascii=False))
        self.assertNotIn(secret, messages[1]["content"])
        self.assertNotIn(
            secret, json.dumps(json.loads(messages[1]["content"]), ensure_ascii=False)
        )
        self.assertIn("JWT_SECRET", messages[1]["content"])

    def test_database_schema_change_during_provider_call_does_not_overwrite_source(
        self,
    ):
        import sqlite3

        def complete(*args, **kwargs):
            with sqlite3.connect(self.project.directory / "data.sqlite") as db:
                db.execute("CREATE TABLE user_change(id INTEGER)")
            return json.dumps(self.plan()), None

        self.complete.side_effect = complete
        with self.assertRaisesRegex(ValueError, "Database definitions changed"):
            self.repair()
        self.assertEqual((self.directory / "endpoints/hello.py").read_text(), BROKEN)

    def test_http_requires_explicit_consent_and_forwards_key_without_exposing_it(self):
        with TestClient(create_app()) as client:
            url = f"/api/projects/{self.project.id}/drafts/{self.draft['id']}/repair"
            blocked = client.post(
                url,
                json=self.request.model_copy(update={"confirm": False}).model_dump(),
            )
            self.assertEqual(blocked.status_code, 409)
            result = client.post(
                url,
                json=self.request.model_dump(),
                headers={"X-Reqlica-Groq-Key": "memory-only-provider-key"},
            )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "repaired")
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertNotIn("memory-only-provider-key", result.text)
        self.assertEqual(
            self.complete.call_args.args[2].get_secret_value(),
            "memory-only-provider-key",
        )
