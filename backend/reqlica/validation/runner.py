"""Disposable trusted-code runner. This process is not a filesystem sandbox."""

import json
import os
import sys
import traceback
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from reqlica.runtime.sdk import configure_database
from reqlica.runtime.worker import build_app

OUTPUT_LIMIT = 16_384
RESULT_LIMIT = 65_536


def run(job: dict) -> dict:
    directory = Path(job["directory"])
    os.chdir(directory)
    sys.path.insert(0, str(directory))
    configure_database(Path(job["database"]))
    app = build_app(directory, database=Path(job["database"]))
    with TestClient(app) as client:
        if job["kind"] == "validation":
            return {
                "status": "passed",
                "output": "Static checks and application loading passed",
                "tests_run": 0,
                "tests_passed": 0,
            }
        if job["kind"] == "call":
            response = client.request(
                job["method"],
                job["path"],
                json=job.get("json_body"),
                follow_redirects=False,
            )
            body = response.text[:OUTPUT_LIMIT]
            try:
                body = json.loads(body)
            except ValueError:
                pass
            return {
                "status": "passed",
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
                "output": "",
                "tests_run": 0,
                "tests_passed": 0,
            }
        tests = directory / "tests"
        if not tests.is_dir():
            return {
                "status": "failed",
                "output": "No tests discovered",
                "tests_run": 0,
                "tests_passed": 0,
            }
        suite = unittest.defaultTestLoader.discover(str(tests), pattern="test*.py")

        class TestResult(unittest.TextTestResult):
            tests_passed = 0

            def addSuccess(self, test):
                super().addSuccess(test)
                self.tests_passed += 1

        result = unittest.TextTestRunner(
            stream=sys.stdout, verbosity=2, resultclass=TestResult
        ).run(suite)
        return {
            "status": "passed"
            if result.wasSuccessful() and result.tests_passed > 0
            else "failed",
            "output": "" if result.tests_passed else "No passing tests discovered",
            "tests_run": result.testsRun,
            "tests_passed": result.tests_passed,
        }


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        result = run(job)
    except (Exception, SystemExit, KeyboardInterrupt):  # noqa: BLE001
        result = {
            "status": "error",
            "output": traceback.format_exc()[-OUTPUT_LIMIT:],
            "tests_run": 0,
            "tests_passed": 0,
        }
    serialized = json.dumps(result)
    if len(serialized.encode("utf-8")) > RESULT_LIMIT:
        serialized = json.dumps(
            {
                "status": "error",
                "output": "Runner result exceeded size limit",
                "tests_run": 0,
                "tests_passed": 0,
            }
        )
    Path(job["result"]).write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
