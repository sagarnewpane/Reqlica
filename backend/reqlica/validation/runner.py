"""Disposable trusted-code runner. This process is not a filesystem sandbox."""

import inspect
import json
import os
import sys
import traceback
import unittest
import warnings
from contextlib import nullcontext
from pathlib import Path

from fastapi.testclient import TestClient
from reqlica.projects.environment import redact_output
from reqlica.runtime.dependencies import dependency_message
from reqlica.runtime.sdk import configure_database
from reqlica.runtime.worker import build_app
from reqlica.validation.contracts import ContractChecks, parse_contract
from reqlica.validation.openapi import generate_openapi

OUTPUT_LIMIT = 16_384
RESULT_LIMIT = 65_536


def _check_async_tests(suite):
    """Reject coroutine tests that ordinary TestCase silently leaves unawaited."""
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            _check_async_tests(test)
        elif isinstance(test, unittest.TestCase) and not isinstance(
            test, unittest.IsolatedAsyncioTestCase
        ):
            for name in (test._testMethodName, "setUp", "tearDown"):
                if inspect.iscoroutinefunction(getattr(test, name, None)):
                    raise ValueError(
                        f"{test.id()}: async {name} requires unittest.IsolatedAsyncioTestCase; "
                        "ordinary TestCase does not await it"
                    )


def run(job: dict) -> dict:
    directory = Path(job["directory"])
    os.chdir(directory)
    sys.path.insert(0, str(directory))
    configure_database(Path(job["database"]))
    app = build_app(directory, database=Path(job["database"]))
    with TestClient(app) as client:
        if job["kind"] == "validation":
            document = generate_openapi(app)
            Path(job["openapi_result"]).write_text(
                json.dumps(document, allow_nan=False), encoding="utf-8"
            )
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
                headers=job.get("headers", {}),
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
        contract = None
        contract_error = None
        try:
            document = parse_contract(job.get("requirements", ""))
            if document is not None:
                contract = ContractChecks(document)
                contract.compare_inputs(app.openapi())
        except ValueError as error:
            contract_error = str(error)

        class TestResult(unittest.TextTestResult):
            tests_passed = 0

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.dependency_errors = []

            def addError(self, test, err):
                if (
                    isinstance(err[1], ModuleNotFoundError)
                    and len(self.dependency_errors) < 20
                ):
                    self.dependency_errors.append(
                        {"message": dependency_message(err[1].name)}
                    )
                super().addError(test, err)

            def addSuccess(self, test):
                super().addSuccess(test)
                self.tests_passed += 1

        with contract.monitor() if contract else nullcontext():
            suite = unittest.defaultTestLoader.discover(str(tests), pattern="test*.py")
            _check_async_tests(suite)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "error",
                    message="It is deprecated to return a value.*",
                    category=DeprecationWarning,
                )
                result = unittest.TextTestRunner(
                    stream=sys.stdout, verbosity=2, resultclass=TestResult
                ).run(suite)
        result_body = {
            "status": "passed"
            if result.wasSuccessful()
            and result.tests_passed >= max(1, job.get("minimum_tests_passed", 1))
            else "failed",
            "output": "" if result.tests_passed else "No passing tests discovered",
            "tests_run": result.testsRun,
            "tests_passed": result.tests_passed,
            "tests_skipped": len(result.skipped),
            "tests_expected_failures": len(result.expectedFailures),
            "failures": ([
                {"test": str(test), "kind": kind, "output": detail}
                for kind, entries in (
                    ("error", result.errors),
                    ("failure", result.failures),
                )
                for test, detail in entries
            ] + [
                {
                    "test": str(test),
                    "kind": "unexpected_success",
                    "output": "Expected-failure test unexpectedly passed; review its marker.",
                }
                for test in result.unexpectedSuccesses
            ])[:20],
        }
        if result.wasSuccessful() and result.tests_passed < job.get(
            "minimum_tests_passed", 1
        ):
            result_body["output"] = (
                "Executed passing coverage fell below the required baseline; original tests must not be skipped or disabled"
            )
        if result.dependency_errors:
            result_body["output"] = result.dependency_errors[0]["message"]
            result_body["dependencies"] = result.dependency_errors
        if contract:
            result_body["contract_checks"] = contract.report()
            if contract.failures:
                result_body["status"] = "failed"
                result_body["failures"] = (contract.failures + result_body["failures"])[
                    :20
                ]
                result_body["output"] += (
                    "\nOriginal OpenAPI contract violations:\n"
                    + "\n".join(
                        failure["test"] + ": " + failure["output"]
                        for failure in contract.failures
                    )
                )
        elif contract_error:
            result_body["status"] = "failed"
            result_body["output"] += "\n" + contract_error
            result_body["failures"] = [
                {
                    "test": "original OpenAPI contract",
                    "kind": "contract",
                    "output": contract_error,
                }
            ] + result_body["failures"][:19]
        else:
            result_body["contract_checks"] = {
                "status": "skipped",
                "warnings": [
                    "No complete original OpenAPI document saved; prose requirements need explicit behavioral tests."
                ],
            }
        return result_body


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if job.get("timezone"):
        import time

        os.environ["TZ"] = job["timezone"]
        time.tzset()
    project_values = {
        name: os.environ[name]
        for name in job.get("environment_names", [])
        if name in os.environ
    }
    try:
        result = run(job)
    except ModuleNotFoundError as error:
        message = redact_output(dependency_message(error.name), project_values)
        result = {
            "status": "error",
            "output": message,
            "dependencies": [{"message": message}],
            "tests_run": 0,
            "tests_passed": 0,
        }
    except (Exception, SystemExit, KeyboardInterrupt):  # noqa: BLE001
        result = {
            "status": "error",
            "output": redact_output(traceback.format_exc(), project_values)[
                -OUTPUT_LIMIT:
            ],
            "tests_run": 0,
            "tests_passed": 0,
        }
    result["output"] = redact_output(result.get("output", ""), project_values)
    for issue in result.get("dependencies", []):
        issue["message"] = redact_output(issue["message"], project_values)
    for failure in result.get("failures", []):
        failure["test"] = redact_output(failure["test"], project_values)[:512]
        failure["output"] = redact_output(failure["output"], project_values)[-2048:]
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
