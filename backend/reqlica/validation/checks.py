"""Static checks and disposable subprocess execution for trusted draft code."""

import ast
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from reqlica.projects.drafts import _contained, _save_state, _state, draft_directory
from reqlica.projects.environment import read_environment, redact_output
from reqlica.projects.store import Manifest, get_project
from reqlica.projects.versions import (
    atomic_write_json,
    content_digest,
    project_lock,
    snapshot_code,
)
from reqlica.runtime.dependencies import import_issue_summary, import_issues
from reqlica.runtime.sdk import backup_database
from reqlica.validation.openapi import OPENAPI_LIMIT
from reqlica.validation.source import name_issues, name_issue_summary

CHECK_TIMEOUT = 10.0
OUTPUT_LIMIT = 16_384
RESULT_LIMIT = 65_536


def static_checks(directory: Path) -> dict:
    """Parse code and route definitions without importing project modules."""
    try:
        manifest_path = _contained(directory, "manifest.json")
        manifest = Manifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        from reqlica.projects.schema_plan import load_plan

        load_plan(directory)
        python_files = set()
        trees = {}
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            if "__pycache__" in relative.parts:
                continue
            _contained(directory, relative.as_posix())
            if path.is_file() and path.suffix == ".py":
                trees[relative.as_posix()] = ast.parse(
                    path.read_bytes(), filename=str(relative)
                )
                python_files.add(relative.as_posix())
        for endpoint in manifest.endpoints:
            filename, _ = endpoint.handler.split(":")
            path = _contained(directory, filename)
            if not path.is_file() or filename not in python_files:
                raise ValueError(f"Missing Python handler: {endpoint.handler}")
        issues = import_issues(trees)
        if issues:
            return {
                "status": "failed",
                "output": import_issue_summary(issues),
                "dependencies": issues,
                "tests_run": 0,
                "tests_passed": 0,
            }
        issues = name_issues(trees)
        if issues:
            return {
                "status": "failed",
                "output": name_issue_summary(issues),
                "source_issues": issues,
                "tests_run": 0,
                "tests_passed": 0,
            }
        return {
            "status": "passed",
            "output": "Static checks passed",
            "tests_run": 0,
            "tests_passed": 0,
        }
    except (OSError, ValueError, SyntaxError) as exc:
        return {
            "status": "failed",
            "output": str(exc)[:OUTPUT_LIMIT],
            "tests_run": 0,
            "tests_passed": 0,
        }


def _execute(job: dict, workspace: Path) -> dict:
    job_path = workspace / "job.json"
    result_path = workspace / "result.json"
    environment = dict(os.environ)
    _, project_values = read_environment(Path(job["environment_directory"]))
    environment.update(project_values)
    atomic_write_json(
        job_path,
        {
            **job,
            "result": str(result_path),
            "environment_names": sorted(project_values),
        },
    )
    # Preserve the backend package location when the child changes to the snapshot.
    backend = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [backend, environment.get("PYTHONPATH", "")])
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    captured = bytearray()
    capture_limit = OUTPUT_LIMIT + max(
        (len(value.encode("utf-8")) for value in project_values.values()), default=0
    )

    def drain(pipe):
        try:
            while chunk := pipe.read(4096):
                remaining = capture_limit - len(captured)
                if remaining > 0:
                    captured.extend(chunk[:remaining])
        finally:
            pipe.close()

    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "reqlica.validation.runner", str(job_path)],
            cwd=job["directory"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "status": "error",
            "output": str(exc)[:OUTPUT_LIMIT],
            "tests_run": 0,
            "tests_passed": 0,
        }
    reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
    reader.start()
    timed_out = False
    try:
        try:
            process.wait(timeout=CHECK_TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        # Also stop descendants left behind after an otherwise successful runner.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        reader.join(timeout=2)
    output = redact_output(captured.decode("utf-8", errors="replace"), project_values)[
        :OUTPUT_LIMIT
    ]
    if timed_out:
        return {
            "status": "timeout",
            "output": (output + "\nExecution timed out")[:OUTPUT_LIMIT],
            "tests_run": 0,
            "tests_passed": 0,
        }
    try:
        if result_path.stat().st_size > RESULT_LIMIT:
            raise ValueError("Runner result exceeded size limit")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or result.get("status") not in {
            "passed",
            "failed",
            "error",
        }:
            raise ValueError("Invalid runner result")
        if process.returncode != 0:
            raise ValueError(f"Runner exited with code {process.returncode}")
        summary = str(result.get("output", ""))
        result["output"] = redact_output(
            summary + "\n" + output if summary else output,
            project_values,
        )[:OUTPUT_LIMIT]
        return result
    except (OSError, ValueError) as exc:
        return {
            "status": "error",
            "output": (output + f"\n{exc}")[:OUTPUT_LIMIT],
            "tests_run": 0,
            "tests_passed": 0,
        }


def _check(
    project_id: str, draft_id: str, expected_revision: str, kind: str, **request
) -> dict:
    project = get_project(project_id)
    with tempfile.TemporaryDirectory(prefix="reqlica-check-") as temporary:
        workspace = Path(temporary).resolve()
        snapshot = workspace / "code"
        database = workspace / "data.sqlite3"
        with project_lock(project.directory):
            checked_state = _state(project, draft_id, expected_revision)
            requirements = (checked_state.get("generation") or {}).get(
                "requirements", ""
            )
            repair = checked_state.get("repair") or {}
            minimum_tests_passed = (
                repair.get("required_passes", 1)
                if repair.get("revision") == expected_revision
                else 1
            )
            environment_revision, _ = read_environment(project.directory)
            revision = snapshot_code(draft_directory(project, draft_id), snapshot)
            if revision != expected_revision:
                raise ValueError("Draft changed while creating the check snapshot")
            backup_database(project.directory / "data.sqlite", database)
        result = static_checks(snapshot)
        if result["status"] == "passed":
            try:
                from reqlica.projects.schema_plan import prepare_database

                prepare_database(snapshot, database)
            except (OSError, ValueError, sqlite3.Error) as error:
                result = {
                    "status": "failed",
                    "output": f"Database setup failed on disposable copy: {error}"[
                        :OUTPUT_LIMIT
                    ],
                    "tests_run": 0,
                    "tests_passed": 0,
                }
        if result["status"] == "passed":
            job = {
                "kind": kind,
                "directory": str(snapshot),
                "database": str(database),
                "environment_directory": str(project.directory),
                "openapi_result": str(workspace / "openapi.json"),
                "minimum_tests_passed": minimum_tests_passed,
                "requirements": requirements,
                **request,
            }
            seed = workspace / "seed.sqlite3"
            if kind == "test" and requirements:
                backup_database(database, seed)
            result = _execute(job, workspace)
            if kind == "test" and requirements:
                # Fresh interpreter, imports, and database for every environmental
                # variant. No host TZ mutation or state leakage across test runs.
                variants = []
                for index, timezone in enumerate(("UTC", "Asia/Kathmandu")):
                    variant_directory = workspace / f"variant-{index}"
                    variant_directory.mkdir()
                    variant_database = variant_directory / "data.sqlite3"
                    try:
                        backup_database(seed, variant_database)
                        variant = _execute(
                            {
                                **job,
                                "database": str(variant_database),
                                "timezone": timezone,
                                "minimum_tests_passed": max(
                                    minimum_tests_passed,
                                    result.get("tests_run", 0)
                                    - result.get("tests_skipped", 0)
                                    - result.get("tests_expected_failures", 0),
                                ),
                            },
                            variant_directory,
                        )
                    except (OSError, ValueError, sqlite3.Error):
                        variant = {
                            "status": "error",
                            "output": "Could not prepare disposable timezone test database",
                            "tests_run": 0,
                            "tests_passed": 0,
                        }
                    variants.append({"timezone": timezone, **variant})
                result["environment_checks"] = [
                    {
                        key: variant[key]
                        for key in ("timezone", "status", "tests_run", "tests_passed")
                    }
                    for variant in variants
                ]
                for variant in variants:
                    if variant["status"] != "passed":
                        result["status"] = "failed"
                        result["output"] = (
                            f"Tests failed under timezone {variant['timezone']}:\n"
                            + variant.get("output", "")
                            + "\n"
                            + result.get("output", "")
                        )[:OUTPUT_LIMIT]
                        failures = variant.get("failures") or [
                            {
                                "test": "environment check",
                                "kind": "environment",
                                "output": variant.get("output", "Check did not pass"),
                            }
                        ]
                        result["failures"] = (
                            [
                                {
                                    **failure,
                                    "test": f"[{variant['timezone']}] {failure['test']}"[:512],
                                    "output": failure.get("output", "")[-2048:],
                                }
                                for failure in failures
                            ]
                            + result.get("failures", [])
                        )[:20]
        try:
            _contained(workspace, "code")
            if (snapshot / "project.json").exists():
                raise ValueError("Snapshot code cannot contain reserved project.json")
            if content_digest(snapshot) != revision:
                raise ValueError("Snapshot content changed during execution")
        except (OSError, ValueError) as exc:
            result = {
                "status": "error",
                "output": f"Execution snapshot integrity check failed: {exc}"[
                    :OUTPUT_LIMIT
                ],
                "tests_run": result.get("tests_run", 0),
                "tests_passed": 0,
            }
        result["revision"] = revision
        result["environment_revision"] = environment_revision
        with project_lock(project.directory):
            state = _state(project, draft_id)
            if read_environment(project.directory)[0] != environment_revision:
                raise ValueError(
                    "Project environment changed during the check; rerun validation and tests"
                )
            if content_digest(draft_directory(project, draft_id)) != revision:
                raise ValueError(
                    "Draft changed during execution; result was not stored"
                )
            if kind in {"validation", "test"}:
                if kind == "validation" and result["status"] == "passed":
                    try:
                        from reqlica.projects.schemas import store_draft_schema

                        schema_path = _contained(workspace, "openapi.json")
                        if schema_path.stat().st_size > OPENAPI_LIMIT:
                            raise ValueError("Generated OpenAPI exceeds its size limit")
                        document = json.loads(schema_path.read_text(encoding="utf-8"))
                        result["schema"] = store_draft_schema(
                            project, draft_id, revision, document
                        )
                    except (OSError, ValueError, TypeError) as error:
                        result.update(
                            status="error",
                            output=(
                                result["output"]
                                + f"\nOpenAPI generation/storage failed: {error}"
                            )[:OUTPUT_LIMIT],
                        )
                state[kind] = result
                state["approval"] = None
                _save_state(project, draft_id, state)
        return result


def validate_draft(project_id: str, draft_id: str, expected_revision: str) -> dict:
    return _check(project_id, draft_id, expected_revision, "validation")


def test_draft(project_id: str, draft_id: str, expected_revision: str) -> dict:
    return _check(project_id, draft_id, expected_revision, "test")


def call_draft_endpoint(
    project_id: str,
    draft_id: str,
    expected_revision: str,
    method: str,
    path: str,
    json_body=None,
    headers: dict[str, str] | None = None,
) -> dict:
    import re

    headers = headers or {}
    forbidden = re.compile(r"^(host|origin|referer|cookie|connection|content-length|transfer-encoding|upgrade|proxy-.*|sec-.*|x-reqlica-.*|x-forwarded-.*)$", re.I)
    if len(headers) > 100 or any(
        not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
        or forbidden.fullmatch(key)
        or not isinstance(value, str)
        or "\r" in value or "\n" in value
        for key, value in headers.items()
    ):
        raise ValueError("Invalid or reserved request header")
    parsed = urlsplit(path)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise ValueError("Endpoint call path must be relative and start with /")
    if method not in {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
        "HEAD",
        "TRACE",
    }:
        raise ValueError("Unsupported endpoint method")
    return _check(
        project_id,
        draft_id,
        expected_revision,
        "call",
        method=method,
        path=path,
        json_body=json_body,
        headers=headers,
    )
