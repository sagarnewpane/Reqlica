"""Static checks and disposable subprocess execution for trusted draft code."""

import ast
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from reqlica.projects.drafts import _contained, _state, _state_path, draft_directory
from reqlica.projects.store import Manifest, get_project
from reqlica.projects.versions import (
    atomic_write_json,
    content_digest,
    project_lock,
    snapshot_code,
)
from reqlica.runtime.sdk import backup_database
from reqlica.validation.openapi import OPENAPI_LIMIT

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
        python_files = set()
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            if "__pycache__" in relative.parts:
                continue
            _contained(directory, relative.as_posix())
            if path.is_file() and path.suffix == ".py":
                ast.parse(path.read_bytes(), filename=str(relative))
                python_files.add(relative.as_posix())
        for endpoint in manifest.endpoints:
            filename, _ = endpoint.handler.split(":")
            path = _contained(directory, filename)
            if not path.is_file() or filename not in python_files:
                raise ValueError(f"Missing Python handler: {endpoint.handler}")
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
    atomic_write_json(job_path, {**job, "result": str(result_path)})
    environment = dict(os.environ)
    # Preserve the backend package location when the child changes to the snapshot.
    backend = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [backend, environment.get("PYTHONPATH", "")])
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    captured = bytearray()

    def drain(pipe):
        try:
            while chunk := pipe.read(4096):
                remaining = OUTPUT_LIMIT - len(captured)
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
    output = captured.decode("utf-8", errors="replace")
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
        result["output"] = (output + str(result.get("output", "")))[:OUTPUT_LIMIT]
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
            _state(project, draft_id, expected_revision)
            revision = snapshot_code(draft_directory(project, draft_id), snapshot)
            if revision != expected_revision:
                raise ValueError("Draft changed while creating the check snapshot")
            backup_database(project.directory / "data.sqlite", database)
        result = static_checks(snapshot)
        if result["status"] == "passed":
            result = _execute(
                {
                    "kind": kind,
                    "directory": str(snapshot),
                    "database": str(database),
                    "openapi_result": str(workspace / "openapi.json"),
                    **request,
                },
                workspace,
            )
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
        with project_lock(project.directory):
            state = _state(project, draft_id)
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
                atomic_write_json(_state_path(project, draft_id), state)
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
) -> dict:
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
    )
