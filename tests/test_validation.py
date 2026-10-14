"""Static parsing, subprocess lifecycle, and disposable SDK database copies."""

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from reqlica.projects.drafts import (
    approve_draft,
    create_draft,
    draft_directory,
    inspect_draft,
    write_draft_file,
)
from reqlica.projects.store import create_project
from reqlica.projects.versions import code_directory, content_digest
from reqlica.validation.checks import (
    OUTPUT_LIMIT,
    call_draft_endpoint,
    static_checks,
    test_draft,
    validate_draft,
)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        environment.start()
        self.addCleanup(environment.stop)
        self.project = create_project(self.root / "project")
        self.draft = create_draft(self.project.id)
        self.directory = draft_directory(self.project, self.draft["id"])

    def write(self, path, content):
        self.draft = write_draft_file(
            self.project.id, self.draft["id"], path, content, self.draft["revision"]
        )

    def arguments(self):
        return (self.project.id, self.draft["id"], self.draft["revision"])

    def test_static_checks_never_import_and_detect_syntax_and_duplicates(self):
        marker = self.root / "imported"
        self.write(
            "endpoints/hello.py",
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef hello():\n    return {{}}\n",
        )
        self.assertEqual(static_checks(self.directory)["status"], "passed")
        self.assertFalse(marker.exists())
        self.write("support.py", "def broken(\n")
        self.assertEqual(static_checks(self.directory)["status"], "failed")
        self.assertEqual(validate_draft(*self.arguments())["status"], "failed")
        self.assertFalse(marker.exists())
        self.write("support.py", "value = 1\n")
        manifest = json.loads((self.directory / "manifest.json").read_text())
        manifest["endpoints"].append(manifest["endpoints"][0])
        self.write("manifest.json", json.dumps(manifest))
        self.assertIn("Duplicate route", static_checks(self.directory)["output"])

    def test_static_checks_reject_missing_and_symlink_handlers(self):
        (self.directory / "endpoints/hello.py").unlink()
        self.assertEqual(static_checks(self.directory)["status"], "failed")
        outside = self.root / "outside.py"
        outside.write_text("def hello():\n    return {}\n")
        (self.directory / "endpoints/hello.py").symlink_to(outside)
        self.assertEqual(static_checks(self.directory)["status"], "failed")

    def test_successful_validation_and_test_results_are_revision_bound(self):
        result = validate_draft(*self.arguments())
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["revision"], self.draft["revision"])
        result = test_draft(*self.arguments())
        self.assertEqual(result["status"], "passed", result)
        self.assertGreaterEqual(result["tests_run"], 1)
        self.assertGreaterEqual(result["tests_passed"], 1)
        inspected = inspect_draft(self.project.id, self.draft["id"])
        self.assertEqual(inspected["test"], result)
        self.assertIsNone(inspected["approval"])

    def test_load_errors_and_failing_tests_are_not_passed(self):
        self.write(
            "endpoints/hello.py",
            "raise RuntimeError('load failure')\ndef hello():\n    return {}\n",
        )
        result = validate_draft(*self.arguments())
        self.assertEqual(result["status"], "error", result)
        self.assertIn("load failure", result["output"])
        self.write("endpoints/hello.py", "def hello():\n    return {}\n")
        self.write(
            "tests/test_hello.py",
            "import unittest\nclass Failure(unittest.TestCase):\n    def test_fail(self):\n        self.fail('intentional test failure')\n",
        )
        result = test_draft(*self.arguments())
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(result["tests_passed"], 0)
        self.assertIn("intentional test failure", result["output"])

    def test_no_tests_is_failure(self):
        self.write("tests/test_hello.py", "# no tests\n")
        result = test_draft(*self.arguments())
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["tests_run"], 0)
        self.assertEqual(result["tests_passed"], 0)

    def test_skips_and_expected_failures_require_an_ordinary_success(self):
        skipped = "    @unittest.skip('not exercised')\n    def test_skip(self):\n        self.fail()\n"
        expected_failure = "    @unittest.expectedFailure\n    def test_expected_failure(self):\n        self.fail()\n"
        success = "    def test_success(self):\n        self.assertTrue(True)\n"
        error = "    def test_error(self):\n        raise RuntimeError('test error')\n"
        unexpected_success = "    @unittest.expectedFailure\n" + success
        for methods, tests_run, tests_passed, status in (
            (skipped, 1, 0, "failed"),
            (expected_failure, 1, 0, "failed"),
            (error, 1, 0, "failed"),
            (unexpected_success, 1, 0, "failed"),
            (success + error, 2, 1, "failed"),
            (skipped + expected_failure + success, 3, 1, "passed"),
        ):
            with self.subTest(tests_run=tests_run, methods=methods):
                self.write(
                    "tests/test_hello.py",
                    "import unittest\nclass Suite(unittest.TestCase):\n" + methods,
                )
                self.assertEqual(validate_draft(*self.arguments())["status"], "passed")
                result = test_draft(*self.arguments())
                self.assertEqual(result["tests_run"], tests_run, result)
                self.assertEqual(result["tests_passed"], tests_passed, result)
                self.assertEqual(result["status"], status)
                if status == "passed":
                    self.assertIsNotNone(approve_draft(*self.arguments())["approval"])
                else:
                    with self.assertRaisesRegex(ValueError, "must pass"):
                        approve_draft(*self.arguments())

    def test_snapshot_mutation_cannot_approve_unchanged_failing_source(self):
        failing_test = "import unittest\nclass Suite(unittest.TestCase):\n    def test_fail(self):\n        self.fail('original failure')\n"
        passing_test = "import unittest\nclass Suite(unittest.TestCase):\n    def test_pass(self):\n        self.assertTrue(True)\n"
        outside = self.root / "passing_test.py"
        outside.write_text(passing_test)
        for mutation in (
            f"target.write_text({passing_test!r})",
            f"target.unlink()\ntarget.symlink_to({str(outside)!r})",
            "(target.parents[1] / 'project.json').write_text('{}')",
        ):
            with self.subTest(mutation=mutation):
                self.write("tests/test_hello.py", failing_test)
                self.write(
                    "endpoints/hello.py",
                    "from pathlib import Path\n"
                    "target = Path(__file__).resolve().parents[1] / 'tests/test_hello.py'\n"
                    + mutation
                    + "\ndef hello():\n    return {}\n",
                )
                original_revision = self.draft["revision"]
                for kind, run in (("validation", validate_draft), ("test", test_draft)):
                    result = run(*self.arguments())
                    self.assertEqual(result["status"], "error", result)
                    self.assertEqual(result["revision"], original_revision)
                    self.assertEqual(result["tests_passed"], 0)
                    self.assertIn("Execution snapshot", result["output"])
                    inspected = inspect_draft(self.project.id, self.draft["id"])
                    self.assertEqual(inspected["revision"], original_revision)
                    self.assertEqual(inspected[kind], result)
                    self.assertIsNone(inspected["approval"])
                self.assertEqual(content_digest(self.directory), original_revision)
                self.assertEqual(
                    (self.directory / "tests/test_hello.py").read_text(), failing_test
                )
                with self.assertRaisesRegex(ValueError, "must pass"):
                    approve_draft(*self.arguments())

    def test_database_copy_includes_wal_and_disposes_import_test_and_call_writes(self):
        database = sqlite3.connect(self.project.directory / "data.sqlite")
        self.addCleanup(database.close)
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE counter (value INTEGER NOT NULL)")
        database.execute("INSERT INTO counter VALUES (1)")
        database.commit()
        source = """from reqlica.runtime.sdk import transaction, connection
with transaction() as database:
    database.execute("UPDATE counter SET value = value + 1")
def hello():
    with transaction() as database:
        database.execute("UPDATE counter SET value = value + 10")
        return {"value": database.execute("SELECT value FROM counter").fetchone()[0]}
"""
        self.write("endpoints/hello.py", source)
        self.write(
            "tests/test_hello.py",
            """import unittest
from endpoints.hello import hello
from reqlica.runtime.sdk import connection, transaction
class DatabaseTest(unittest.TestCase):
    def test_copy(self):
        self.assertTrue(callable(hello))
        with connection() as database:
            self.assertEqual(database.execute("SELECT value FROM counter").fetchone()[0], 2)
        with transaction() as database:
            database.execute("UPDATE counter SET value = 99")
""",
        )
        live_revision = content_digest(code_directory(self.project))
        for run in (validate_draft, test_draft):
            result = run(*self.arguments())
            self.assertEqual(result["status"], "passed", result)
            self.assertEqual(
                database.execute("SELECT value FROM counter").fetchone()[0], 1
            )
        for _ in range(2):
            result = call_draft_endpoint(*self.arguments(), "GET", "/hello")
            self.assertEqual(result["status"], "passed", result)
            self.assertEqual(result["status_code"], 200)
            self.assertEqual(result["body"], {"value": 12})
        self.assertEqual(database.execute("SELECT value FROM counter").fetchone()[0], 1)
        self.assertEqual(content_digest(code_directory(self.project)), live_revision)

    def test_call_paths_are_relative_and_redirects_are_not_followed(self):
        for path in (
            "https://example.com/hello",
            "//example.com/hello",
            "hello",
            "/\\example.com",
            "/hello\n",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                call_draft_endpoint(*self.arguments(), "GET", path)
        self.write(
            "endpoints/hello.py",
            "from fastapi.responses import RedirectResponse\ndef hello():\n    return RedirectResponse('https://example.com/')\n",
        )
        result = call_draft_endpoint(*self.arguments(), "GET", "/hello")
        self.assertEqual(result["status_code"], 307, result)

    def test_timeouts_kill_runner_and_its_descendants(self):
        marker = self.root / "descendant-finished"
        child = f"import time; from pathlib import Path; time.sleep(1.5); Path({str(marker)!r}).touch()"
        self.write(
            "endpoints/hello.py",
            f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {child!r}])\ntime.sleep(30)\ndef hello():\n    return {{}}\n",
        )
        with patch("reqlica.validation.checks.CHECK_TIMEOUT", 1.0):
            result = validate_draft(*self.arguments())
        self.assertEqual(result["status"], "timeout", result)
        time.sleep(1.6)
        self.assertFalse(marker.exists())

    def test_abrupt_exit_and_output_are_bounded(self):
        self.write(
            "endpoints/hello.py",
            "import os\nprint('x' * 200000, flush=True)\nos._exit(7)\ndef hello():\n    return {}\n",
        )
        result = validate_draft(*self.arguments())
        self.assertEqual(result["status"], "error", result)
        self.assertLessEqual(len(result["output"]), OUTPUT_LIMIT)

    def test_source_change_during_execution_does_not_store_results_or_hold_lock(self):
        old_revision = self.draft["revision"]

        def execute(job, workspace):
            self.assertEqual(content_digest(Path(job["directory"])), old_revision)
            errors = []

            def edit():
                try:
                    self.write("support.py", "value = 1\n")
                except (OSError, ValueError) as exc:
                    errors.append(exc)

            thread = threading.Thread(target=edit, daemon=True)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(
                thread.is_alive(), "Project lock was held during execution"
            )
            self.assertEqual(errors, [])
            self.assertEqual(content_digest(Path(job["directory"])), old_revision)
            return {"status": "passed", "output": "", "tests_run": 0, "tests_passed": 0}

        with (
            patch("reqlica.validation.checks._execute", side_effect=execute),
            self.assertRaisesRegex(ValueError, "changed during execution"),
        ):
            validate_draft(self.project.id, self.draft["id"], old_revision)
        inspected = inspect_draft(self.project.id, self.draft["id"])
        self.assertIsNone(inspected["validation"])
        self.assertNotEqual(inspected["revision"], old_revision)
