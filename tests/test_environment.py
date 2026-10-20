"""Private project configuration, child-process injection, and JWT-key stability."""

import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from reqlica.console.app import create_app
from reqlica.generation.prompts import build_messages
from reqlica.generation.schemas import GenerationRequest
from reqlica.projects import drafts
from reqlica.projects.context import get_project_context
from reqlica.projects.environment import (
    EMPTY_REVISION,
    ENV_PATH,
    change_environment,
    get_environment,
    read_environment,
)
from reqlica.projects.store import Endpoint, create_project
from reqlica.projects.versions import code_directory, content_digest, snapshot_code
from reqlica.runtime.worker import WorkerManager
from reqlica.validation import checks

NAME = "REQLICA_JWT_SECRET"
SECRET = "test-only-local-secret-" + "a" * 48

HANDLER = """import base64
import hashlib
import hmac
import json
import os
import time
from fastapi import HTTPException
from pydantic import BaseModel

# Deliberately read at import time to verify injection happens before imports.
KEY = os.environ.get("REQLICA_JWT_SECRET")

class Token(BaseModel):
    access_token: str

def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def token() -> Token:
    if not KEY:
        raise HTTPException(503, "Configure REQLICA_JWT_SECRET in project settings")
    now = int(time.time())
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = encode(json.dumps({"sub": "1", "iat": now, "exp": now + 900}).encode())
    message = header + "." + payload
    signature = encode(hmac.new(KEY.encode(), message.encode(), hashlib.sha256).digest())
    return Token(access_token=message + "." + signature)

def verify(body: Token) -> dict[str, bool]:
    if not KEY:
        return {"valid": False}
    try:
        header, payload, signature = body.access_token.split(".")
        expected = encode(hmac.new(KEY.encode(), (header + "." + payload).encode(), hashlib.sha256).digest())
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return {"valid": hmac.compare_digest(signature, expected) and claims["exp"] > time.time()}
    except (ValueError, KeyError):
        return {"valid": False}
"""

TEST_SOURCE = """import unittest
from endpoints.configured import KEY, token, verify

class ConfiguredTests(unittest.TestCase):
    def test_environment_available_before_import(self):
        self.assertIsNotNone(KEY)
        self.assertGreaterEqual(len(KEY), 32)

    def test_real_hmac_token_roundtrip(self):
        self.assertEqual(verify(token()), {"valid": True})

    def test_plain_output_is_redacted(self):
        print(KEY)
"""


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")})
        )
        os.environ.pop(NAME, None)
        self.project = create_project(self.root / "auth", "Configured Auth")
        self.other = create_project(self.root / "other", "Other project")

    def save(self, value=SECRET, name=NAME, project=None):
        project = project or self.project
        return change_environment(
            project.id, name, get_environment(project.id)["revision"], SecretStr(value)
        )

    def configured_draft(self):
        self.save()
        draft = drafts.create_draft(self.project.id)
        for filename, source in (
            ("endpoints/configured.py", HANDLER),
            ("tests/test_configured.py", TEST_SOURCE),
        ):
            draft = drafts.write_draft_file(
                self.project.id, draft["id"], filename, source, draft["revision"]
            )
        for method, path, function in (
            ("GET", "/token", "token"),
            ("POST", "/verify", "verify"),
        ):
            draft = drafts.upsert_endpoint(
                self.project.id,
                draft["id"],
                Endpoint(
                    method=method,
                    path=path,
                    handler=f"endpoints/configured.py:{function}",
                ),
                draft["revision"],
            )
        return draft

    def check(self, draft):
        args = self.project.id, draft["id"], draft["revision"]
        validation = drafts.validate_draft(*args)
        self.assertEqual(validation["status"], "passed", validation)
        result = drafts.test_draft(*args)
        self.assertEqual(result["status"], "passed", result)
        self.assertNotIn(SECRET, result["output"])
        self.assertIn("[REDACTED]", result["output"])
        return args

    def test_values_are_private_project_scoped_and_not_put_in_parent_environment(self):
        before = os.environ.get(NAME)
        result = self.save()
        self.assertEqual(result["names"], [NAME])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(os.environ.get(NAME), before)
        self.assertEqual(get_environment(self.other.id)["names"], [])
        path = self.project.directory / ENV_PATH
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(read_environment(self.project.directory)[1][NAME], SECRET)

    def test_generate_saves_a_stable_secure_value_without_returning_it(self):
        result = change_environment(
            self.project.id, NAME, EMPTY_REVISION, generate=True
        )
        revision, values = read_environment(self.project.directory)
        self.assertEqual(len(values[NAME]), 64)
        self.assertEqual(result["revision"], revision)
        self.assertNotIn(values[NAME], json.dumps(result))
        self.assertEqual(read_environment(self.project.directory)[1], values)

    def test_revision_conflicts_and_removal_do_not_overwrite_other_settings(self):
        self.save()
        with self.assertRaisesRegex(ValueError, "settings changed"):
            change_environment(
                self.project.id, NAME, EMPTY_REVISION, SecretStr("not saved")
            )
        self.save("app-mode", "APP_MODE")
        result = change_environment(
            self.project.id,
            NAME,
            get_environment(self.project.id)["revision"],
            remove=True,
        )
        self.assertEqual(result["names"], ["APP_MODE"])
        self.assertNotIn(NAME, read_environment(self.project.directory)[1])

    def test_reserved_names_invalid_values_and_size_limits_are_rejected(self):
        for name in (
            "PATH",
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "REQLICA_HOME",
            "GROQ_API_KEY",
            "bad-name",
            "../key",
            "1KEY",
            "x" * 65,
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.save(name=name)
        for value in ("", "has\0nul", "x" * 8193):
            with self.subTest(value=value[:20]), self.assertRaises(ValueError):
                self.save(value=value)
        self.assertEqual(get_environment(self.project.id)["names"], [])

    def test_configuration_is_excluded_from_source_snapshots_context_and_model_messages(
        self,
    ):
        self.save()
        context = get_project_context(self.project.id)
        self.assertNotIn(ENV_PATH, context["files"])
        self.assertEqual(context["environment"]["names"], [NAME])
        self.assertNotIn(SECRET, json.dumps(context))
        _, messages = build_messages(
            self.project.id,
            GenerationRequest(requirements="Use the configured JWT key"),
        )
        self.assertIn(NAME, json.dumps(messages))
        self.assertNotIn(SECRET, json.dumps(messages))
        destination = self.root / "snapshot"
        snapshot_code(code_directory(self.project), destination)
        self.assertFalse((destination / ENV_PATH).exists())

    def test_checks_use_private_values_before_import_and_redact_plain_output(self):
        draft = self.configured_draft()
        before = content_digest(code_directory(self.project))
        args = self.check(draft)
        self.assertEqual(content_digest(code_directory(self.project)), before)
        result = drafts.call_draft_endpoint(*args, "GET", "/token")
        self.assertEqual(result["status_code"], 200, result)
        token = result["body"]["access_token"]
        self.assertTrue(self.valid_signature(token, SECRET))

    def test_environment_changes_invalidate_checks_and_approval_not_source_revision(
        self,
    ):
        draft = self.configured_draft()
        args = self.check(draft)
        drafts.approve_draft(*args)
        self.save("different-test-secret-" + "b" * 48)
        state = drafts.inspect_draft(self.project.id, draft["id"])
        self.assertEqual(state["revision"], draft["revision"])
        self.assertIsNone(state["validation"])
        self.assertIsNone(state["test"])
        self.assertIsNone(state["approval"])
        with self.assertRaisesRegex(ValueError, "Validation and tests"):
            drafts.require_approved(*args)

    def test_environment_changed_during_execution_cannot_store_passing_results(self):
        draft = self.configured_draft()
        execute = checks._execute

        def change_during_check(job, workspace):
            result = execute(job, workspace)
            self.save("new-test-secret-" + "c" * 48)
            return result

        with (
            patch(
                "reqlica.validation.checks._execute", side_effect=change_during_check
            ),
            self.assertRaisesRegex(ValueError, "environment changed"),
        ):
            drafts.validate_draft(self.project.id, draft["id"], draft["revision"])
        self.assertIsNone(
            drafts.inspect_draft(self.project.id, draft["id"])["validation"]
        )

    @staticmethod
    def valid_signature(token, secret):
        header, payload, signature = token.split(".")
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(
                    secret.encode(), (header + "." + payload).encode(), hashlib.sha256
                ).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        return hmac.compare_digest(expected, signature)

    def test_real_workers_keep_jwt_key_stable_until_explicit_restart_and_isolate_projects(
        self,
    ):
        self.save()
        for project in (self.project, self.other):
            code = code_directory(project)
            (code / "endpoints/configured.py").write_text(HANDLER)
            manifest = {
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/token",
                        "handler": "endpoints/configured.py:token",
                    },
                    {
                        "method": "POST",
                        "path": "/verify",
                        "handler": "endpoints/configured.py:verify",
                    },
                ]
            }
            (code / "manifest.json").write_text(json.dumps(manifest))
        manager = WorkerManager()
        self.addCleanup(manager.close)
        worker = manager.get_or_start(self.project.id)
        url = f"http://127.0.0.1:{worker.port}"
        with httpx.Client(trust_env=False) as client:
            token = client.get(url + "/token").json()["access_token"]
            self.assertTrue(self.valid_signature(token, SECRET))
            self.assertEqual(
                client.post(url + "/verify", json={"access_token": token}).json(),
                {"valid": True},
            )
            next_secret = "rotated-test-key-" + "d" * 48
            metadata = self.save(next_secret)
            self.assertNotEqual(
                manager.describe_project(self.project.id)["environment_revision"],
                metadata["revision"],
            )
            self.assertTrue(
                self.valid_signature(
                    client.get(url + "/token").json()["access_token"], SECRET
                )
            )
            manager.stop(self.project.id)
            manager.start(self.project.id)
            worker = manager.get_or_start(self.project.id)
            url = f"http://127.0.0.1:{worker.port}"
            self.assertTrue(
                self.valid_signature(
                    client.get(url + "/token").json()["access_token"], next_secret
                )
            )
            self.assertEqual(
                client.post(url + "/verify", json={"access_token": token}).json(),
                {"valid": False},
            )
            self.assertEqual(
                manager.describe_project(self.project.id)["environment_revision"],
                metadata["revision"],
            )
            other = manager.get_or_start(self.other.id)
            self.assertEqual(
                client.get(f"http://127.0.0.1:{other.port}/token").status_code, 503
            )

    def test_worker_startup_errors_do_not_echo_private_values(self):
        self.save()
        (code_directory(self.project) / "endpoints/hello.py").write_text(
            f"import os\nraise RuntimeError(os.environ[{NAME!r}])\n"
        )
        manager = WorkerManager()
        self.addCleanup(manager.close)
        with self.assertRaises(RuntimeError) as caught:
            manager.start(self.project.id)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))
        self.assertNotIn(SECRET, json.dumps(manager.describe_project(self.project.id)))

    def test_malformed_private_file_errors_are_sanitized(self):
        path = self.project.directory / ENV_PATH
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"version":' + SECRET)
        with self.assertRaises(ValueError) as caught:
            read_environment(self.project.directory)
        self.assertNotIn(SECRET, str(caught.exception))

    def test_private_settings_reject_symlink_traversal(self):
        outside = self.root / "outside-secret.json"
        outside.write_text(SECRET)
        (self.project.directory / ENV_PATH).parent.mkdir(exist_ok=True)
        (self.project.directory / ENV_PATH).symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            get_environment(self.project.id)

    def test_private_values_are_not_serialized_into_check_job_files(self):
        draft = self.configured_draft()
        execute = checks._execute
        jobs = []

        def inspect_job(job, workspace):
            result = execute(job, workspace)
            jobs.append((workspace / "job.json").read_text())
            return result

        with patch("reqlica.validation.checks._execute", side_effect=inspect_job):
            self.check(draft)
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(SECRET not in job for job in jobs))

    def test_error_truncation_does_not_expose_a_partial_secret(self):
        draft = self.configured_draft()
        source = f"import os\nraise RuntimeError(os.environ[{NAME!r}] + 'x' * 16360)\n"
        draft = drafts.write_draft_file(
            self.project.id,
            draft["id"],
            "endpoints/configured.py",
            source,
            draft["revision"],
        )
        result = drafts.validate_draft(self.project.id, draft["id"], draft["revision"])
        self.assertEqual(result["status"], "error")
        self.assertNotIn(SECRET[-20:], result["output"])

    def test_http_values_are_write_only_even_in_validation_errors(self):
        with TestClient(create_app()) as client:
            root = f"/api/projects/{self.project.id}/environment"
            initial = client.get(root)
            self.assertEqual(initial.status_code, 200)
            result = client.put(
                root + "/" + NAME,
                json={"expected_revision": initial.json()["revision"], "value": SECRET},
            )
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.headers["cache-control"], "no-store")
            self.assertNotIn(SECRET, result.text)
            self.assertNotIn(SECRET, client.get(root).text)
            for body in (
                {"value": SECRET},
                {"expected_revision": "invalid", "value": SECRET},
                {
                    "expected_revision": result.json()["revision"],
                    "value": SECRET + "\0",
                },
                {
                    "expected_revision": result.json()["revision"],
                    "value": SECRET,
                    "extra": SECRET,
                },
            ):
                response = client.put(root + "/" + NAME, json=body)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(SECRET, response.text)
            generated = client.post(
                root + "/OTHER_KEY/generate",
                json={"expected_revision": result.json()["revision"]},
            )
            self.assertEqual(generated.status_code, 200, generated.text)
            self.assertIn("OTHER_KEY", generated.json()["names"])
