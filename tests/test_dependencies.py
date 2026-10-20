"""Dependency-aware planning, non-executing preflight, and explicit installation."""

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr
from reqlica.generation.clarification import review_requirements
from reqlica.generation.errors import GenerationError
from reqlica.generation.prompts import build_messages
from reqlica.generation.schemas import (
    ClarificationRequest,
    EndpointEditRequest,
    GenerationRequest,
)
from reqlica.generation.service import (
    edit_endpoint,
    endpoint_source,
    generate_endpoints,
)
from reqlica.projects import drafts
from reqlica.projects.environment import change_environment, get_environment
from reqlica.projects.store import create_project
from reqlica.projects.versions import code_directory, content_digest
from reqlica.runtime import dependencies
from reqlica.runtime.dependencies import (
    DEPENDENCY_PROMPT,
    dependency_message,
    import_issues,
)
from reqlica.runtime.worker import WorkerManager


class DependencyPolicyTests(unittest.TestCase):
    def test_only_declared_distributions_not_incidental_installs_are_approved(self):
        dependencies._declared_modules.cache_clear()
        self.addCleanup(dependencies._declared_modules.cache_clear)
        with (
            patch.object(
                dependencies.metadata,
                "requires",
                return_value=[
                    "bcrypt>=5",
                    "fastapi[standard]>=0.142",
                    'optional; extra == "feature"',
                ],
            ),
            patch.object(
                dependencies.metadata,
                "packages_distributions",
                return_value={
                    "bcrypt": ["bcrypt"],
                    "fastapi": ["fastapi"],
                    "jwt": ["PyJWT"],
                    "optional": ["optional"],
                },
            ),
            patch.object(dependencies, "_available", return_value=True),
        ):
            policy = dependencies.runtime_dependency_policy()
        self.assertEqual(policy["available"]["bcrypt"], "bcrypt")
        self.assertNotIn("jwt", policy["available"])
        self.assertNotIn("optional", policy["available"])

    def test_missing_declared_library_is_not_offered(self):
        with (
            patch.object(
                dependencies,
                "_declared_modules",
                return_value=(("bcrypt", "bcrypt"), ("jwt", "PyJWT")),
            ),
            patch.object(
                dependencies, "_available", side_effect=lambda root: root == "bcrypt"
            ),
        ):
            policy = dependencies.runtime_dependency_policy()
        self.assertEqual(policy["available"], {"bcrypt": "bcrypt"})
        self.assertEqual(policy["missing_declared"], {"jwt": "PyJWT"})

    def check(self, source, *, approved=True, others=None):
        trees = {"endpoints/auth.py": ast.parse(source), **(others or {})}
        return import_issues(
            trees,
            approved_only=approved,
            policy={
                "available": {"fastapi": "fastapi", "pydantic": "pydantic"},
                "missing_declared": {},
            },
        )

    def test_stdlib_sdk_relative_and_namespace_imports_are_supported(self):
        issues = self.check(
            "import hashlib, secrets, hmac\nfrom fastapi import HTTPException\nfrom reqlica.runtime.sdk import connection\nfrom .helpers import verify\nfrom models.users import User\n",
            others={
                "endpoints/helpers.py": ast.parse("def verify(): pass"),
                "models/users.py": ast.parse("class User: pass"),
            },
        )
        self.assertEqual(issues, [])

    def test_unsupported_imports_inside_functions_and_tests_are_detected(self):
        for source in (
            "import notapproved",
            "from notapproved.auth import verify",
            "def login():\n    import notapproved",
            "try:\n    import notapproved\nexcept ImportError:\n    pass",
        ):
            with (
                self.subTest(source=source),
                patch.object(dependencies, "_available", return_value=True),
            ):
                issues = self.check(source)
                self.assertEqual(issues[0]["code"], "unsupported_dependency")
                self.assertEqual(issues[0]["line"], 2 if "\n" in source else 1)

    def test_missing_local_modules_do_not_suggest_installing_packages(self):
        issues = self.check(
            "from models.missing import User", others={"models/users.py": ast.parse("")}
        )
        self.assertEqual(issues[0]["code"], "missing_local_module")
        self.assertNotIn("uv add", issues[0]["message"])
        self.assertNotIn("uv add", dependency_message("endpoints.missing"))

    def test_management_modules_are_not_exposed_to_generated_code(self):
        for source in (
            "from reqlica.projects.store import get_project",
            "from reqlica.runtime import worker",
            "import reqlica",
        ):
            with self.subTest(source=source):
                issues = self.check(source)
                self.assertEqual(issues[0]["code"], "unsupported_dependency")
                self.assertIn("internal management", issues[0]["message"])

    def test_manual_optional_and_type_only_imports_are_not_false_failures(self):
        source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import unavailable\ntry:\n    import unavailable\nexcept ImportError:\n    pass\n"
        self.assertEqual(self.check(source, approved=False), [])

    def test_diagnostics_use_known_distribution_names_not_arbitrary_commands(self):
        self.assertIn("uv add PyJWT", dependency_message("jwt"))
        self.assertNotIn("uv add jwt", dependency_message("jwt"))
        self.assertIn("uv add bcrypt", dependency_message("bcrypt"))
        self.assertNotIn("uv add", dependency_message("unreviewed_unknown"))
        self.assertNotIn(";", dependency_message("bcrypt; touch /outside"))
        self.assertNotIn("touch", dependency_message("bcrypt; touch /outside"))
        self.assertIn(
            "Missing Python submodule", dependency_message("fastapi.does_not_exist")
        )
        self.assertNotIn("uv add", dependency_message("fastapi.does_not_exist"))

    def test_inventory_probe_does_not_execute_package_initializers(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            marker = directory / "executed"
            package = directory / "thirdparty_probe"
            package.mkdir()
            (package / "__init__.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            )
            with patch.object(sys, "path", [str(directory), *sys.path]):
                self.assertTrue(dependencies._available("thirdparty_probe"))
            self.assertFalse(marker.exists())


class DependencyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")})
        )
        self.project = create_project(self.root / "project")
        self.request = GenerationRequest(requirements="Add an authenticated endpoint")

    def plan(self, source):
        return {
            "summary": "Add an auth endpoint",
            "assumptions": [],
            "files": [
                {
                    "path": "endpoints/auth.py",
                    "content": source + "\ndef auth(): return {}\n",
                },
                {
                    "path": "tests/test_auth.py",
                    "content": "import unittest\nclass AuthTests(unittest.TestCase):\n    def test_example(self): self.assertTrue(True)\n",
                },
            ],
            "endpoints": [
                {"method": "GET", "path": "/auth", "handler": "endpoints/auth.py:auth"}
            ],
        }

    def test_generation_rejects_dependencies_before_draft_creation_or_execution(self):
        marker = self.root / "executed"
        source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\nimport definitely_not_declared_auth_library\n"
        before = content_digest(code_directory(self.project))
        with (
            patch(
                "reqlica.generation.service.provider.complete",
                return_value=(json.dumps(self.plan(source)), None),
            ) as complete,
            self.assertRaises(GenerationError) as raised,
        ):
            generate_endpoints(self.project.id, self.request)
        self.assertEqual(raised.exception.detail["code"], "dependency_error")
        self.assertEqual(
            raised.exception.detail["dependencies"][0]["module"],
            "definitely_not_declared_auth_library",
        )
        self.assertIn("No draft was created", str(raised.exception))
        self.assertFalse(marker.exists())
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])
        self.assertEqual(content_digest(code_directory(self.project)), before)
        complete.assert_called_once()

    def test_unsupported_import_in_generated_test_also_blocks_the_proposal(self):
        plan = self.plan("")
        plan["files"][1]["content"] = (
            "import notdeclared\n" + plan["files"][1]["content"]
        )
        with (
            patch(
                "reqlica.generation.service.provider.complete",
                return_value=(json.dumps(plan), None),
            ),
            self.assertRaises(GenerationError),
        ):
            generate_endpoints(self.project.id, self.request)
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_undefined_names_block_generation_without_executing_source(self):
        plan = self.plan("")
        plan["files"][0]["content"] = "def auth(): return missing_response\n"
        with (
            patch("reqlica.generation.service.provider.complete",
                  return_value=(json.dumps(plan), None)),
            self.assertRaisesRegex(GenerationError, "missing_response"),
        ):
            generate_endpoints(self.project.id, self.request)
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_clarification_and_generation_share_policy_and_instructions(self):
        _, generation = build_messages(self.project.id, self.request)
        with patch(
            "reqlica.generation.clarification.provider.complete",
            return_value=(json.dumps({"summary": "Ready", "questions": []}), None),
        ) as complete:
            review_requirements(
                self.project.id,
                ClarificationRequest(requirements=self.request.requirements),
            )
        clarification = complete.call_args.args[0]
        self.assertIn(DEPENDENCY_PROMPT, generation[0]["content"])
        self.assertIn(DEPENDENCY_PROMPT, clarification[0]["content"])
        policy = json.loads(generation[1]["content"])["runtime_dependencies"]
        self.assertEqual(
            policy, json.loads(clarification[1]["content"])["runtime_dependencies"]
        )
        self.assertEqual(policy["available"]["fastapi"], "fastapi")
        self.assertIn("fastapi", policy["package_versions"])

    def test_dependency_policy_change_requires_fresh_clarification(self):
        with patch(
            "reqlica.generation.clarification.provider.complete",
            return_value=(json.dumps({"summary": "Ready", "questions": []}), None),
        ):
            review = review_requirements(
                self.project.id,
                ClarificationRequest(requirements=self.request.requirements),
            )
        context, _ = build_messages(self.project.id, self.request)
        changed = context["runtime_dependencies"]
        changed["available"]["new_dependency"] = "new-dependency"
        request = GenerationRequest(
            requirements=self.request.requirements,
            clarification={
                "context_fingerprint": review["context_fingerprint"],
                "decisions": [],
            },
        )
        with (
            patch(
                "reqlica.projects.context.runtime_dependency_policy",
                return_value=changed,
            ),
            patch("reqlica.generation.service.provider.complete") as complete,
            self.assertRaisesRegex(ValueError, "review clarification"),
        ):
            generate_endpoints(self.project.id, request)
        complete.assert_not_called()

    def test_endpoint_edits_receive_the_same_preflight(self):
        target = endpoint_source(self.project.id, "GET", "/hello")
        plan = self.plan("import definitely_not_declared_auth_library")
        plan["files"][0]["path"] = "endpoints/hello.py"
        plan["files"][0]["content"] = (
            "import definitely_not_declared_auth_library\ndef hello(): return {}\n"
        )
        plan["endpoints"] = [target["endpoint"]]
        request = EndpointEditRequest(
            requirements="Update this route",
            endpoint=target["endpoint"],
            expected_revision=target["revision"],
        )
        with (
            patch(
                "reqlica.generation.service.provider.complete",
                return_value=(json.dumps(plan), None),
            ),
            self.assertRaises(GenerationError) as raised,
        ):
            edit_endpoint(self.project.id, request)
        self.assertEqual(raised.exception.detail["code"], "dependency_error")
        self.assertEqual(drafts.list_drafts(self.project.id)["drafts"], [])

    def test_existing_draft_missing_import_fails_before_any_handler_executes(self):
        draft = drafts.create_draft(self.project.id)
        marker = self.root / "executed"
        draft = drafts.write_draft_file(
            self.project.id,
            draft["id"],
            "endpoints/hello.py",
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\nimport definitely_missing_auth_package\ndef hello(): return {{}}\n",
            draft["revision"],
        )
        with patch(
            "reqlica.validation.checks._execute",
            side_effect=AssertionError("must not execute"),
        ):
            result = drafts.validate_draft(
                self.project.id, draft["id"], draft["revision"]
            )
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("Missing Python dependency", result["output"])
        self.assertNotIn("Traceback", result["output"])
        self.assertEqual(result["dependencies"][0]["line"], 3)
        self.assertEqual(result["tests_run"], 0)
        self.assertFalse(marker.exists())

    def test_dynamic_missing_import_has_actionable_runner_error(self):
        draft = drafts.create_draft(self.project.id)
        draft = drafts.write_draft_file(
            self.project.id,
            draft["id"],
            "endpoints/hello.py",
            "__import__('definitely_missing_dynamic_package')\ndef hello(): return {}\n",
            draft["revision"],
        )
        result = drafts.validate_draft(self.project.id, draft["id"], draft["revision"])
        self.assertEqual(result["status"], "error", result)
        self.assertIn(
            "Missing Python dependency: definitely_missing_dynamic_package",
            result["output"],
        )
        self.assertNotIn("Traceback", result["output"])

    def test_reported_dependency_works_in_disposable_checks(self):
        source = "import httpx\n"
        plan = self.plan(source)
        plan["files"][1]["content"] = (
            "import unittest\nimport httpx\nclass DependencyTests(unittest.TestCase):\n    def test_response_decoding(self):\n        response = httpx.Response(201, json={'created': True})\n        self.assertEqual(response.status_code, 201)\n        self.assertEqual(response.json(), {'created': True})\n"
        )
        with patch(
            "reqlica.generation.service.provider.complete",
            return_value=(json.dumps(plan), None),
        ):
            result = generate_endpoints(self.project.id, self.request)
        draft = result["draft"]
        args = self.project.id, draft["id"], draft["revision"]
        self.assertEqual(drafts.validate_draft(*args)["status"], "passed")
        tested = drafts.test_draft(*args)
        self.assertEqual(tested["status"], "passed", tested)
        self.assertGreater(tested["tests_passed"], 0)

    def test_missing_dynamic_test_dependency_has_summary_and_preserves_failure(self):
        draft = drafts.create_draft(self.project.id)
        draft = drafts.write_draft_file(
            self.project.id,
            draft["id"],
            "tests/test_hello.py",
            "import unittest\nclass DynamicTest(unittest.TestCase):\n    def test_missing(self):\n        __import__('definitely_missing_dynamic_test_library')\n",
            draft["revision"],
        )
        result = drafts.test_draft(self.project.id, draft["id"], draft["revision"])
        self.assertEqual(result["status"], "failed", result)
        self.assertTrue(
            result["output"].startswith("Missing Python dependency:"), result["output"]
        )
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(result["tests_passed"], 0)
        self.assertIn(
            "definitely_missing_dynamic_test_library",
            result["dependencies"][0]["message"],
        )

    def test_worker_startup_reports_missing_dependency_without_traceback(self):
        (code_directory(self.project) / "endpoints/hello.py").write_text(
            "import definitely_missing_worker_package\ndef hello(): return {}\n"
        )
        manager = WorkerManager()
        self.addCleanup(manager.close)
        with self.assertRaises(RuntimeError) as raised:
            manager.start(self.project.id)
        self.assertIn(
            "Missing Python dependency: definitely_missing_worker_package",
            str(raised.exception),
        )
        self.assertIn("No package was installed automatically", str(raised.exception))
        self.assertNotIn("Traceback", str(raised.exception))

    def test_dependency_diagnostics_redact_environment_values_before_result_storage(
        self,
    ):
        from reqlica.validation.checks import _execute

        secret = "private_dependency_name_from_environment"
        change_environment(
            self.project.id,
            "JWT_SECRET",
            get_environment(self.project.id)["revision"],
            SecretStr(secret),
        )
        draft = drafts.create_draft(self.project.id)
        draft = drafts.write_draft_file(
            self.project.id,
            draft["id"],
            "tests/test_hello.py",
            "import os, unittest\nclass DynamicTest(unittest.TestCase):\n    def test_missing(self):\n        __import__(os.environ['JWT_SECRET'])\n",
            draft["revision"],
        )
        files = []

        def inspect_result(job, workspace):
            result = _execute(job, workspace)
            files.append((workspace / "result.json").read_text())
            return result

        with patch("reqlica.validation.checks._execute", side_effect=inspect_result):
            result = drafts.test_draft(self.project.id, draft["id"], draft["revision"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, files[0])
