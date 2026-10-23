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
from reqlica.projects import drafts
from reqlica.projects.environment import change_environment, get_environment
from reqlica.projects.store import create_project
from reqlica.projects.versions import code_directory
from reqlica.runtime import dependencies
from reqlica.runtime.dependencies import dependency_message, import_issues
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
