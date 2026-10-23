"""Mock authentication persists per project and rejects traffic before execution."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.projects.authentication import AUTH_PATH
from reqlica.projects.store import create_project
from reqlica.runtime.app import app as gateway


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, REQLICA_HOME=str(self.root / "home")))
        self.project = create_project(self.root / "api")
        self.other = create_project(self.root / "other")
        self.console = self.enterContext(TestClient(create_app()))
        self.gateway = self.enterContext(TestClient(gateway))
        self.url = f"/api/projects/{self.project.id}/mock-auth"
        self.mock = f"/mock/{self.project.id}/hello"

    def configure(self, mode="bearer", credentials=None, **kwargs):
        state = self.console.get(self.url).json()
        payload = {"expected_revision": state["revision"], "mode": mode,
                   "credentials": credentials if credentials is not None else [{"name": "tester", "value": "test-token"}], **kwargs}
        return self.console.put(self.url, json=payload)

    def test_bearer_invalid_expired_and_no_execution_on_rejection(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        response = self.configure(credentials=[{"name":"valid", "value":"good"}, {"name":"old", "value":"expired", "expires_at":old}])
        self.assertEqual(response.status_code, 200, response.text)
        with patch.object(gateway.state.proxy_client, "send", side_effect=AssertionError("must not forward")):
            for headers, code in [({}, "missing_credentials"), ({"Authorization":"Basic good"}, "invalid_credentials"),
                                  ({"Authorization":"Bearer wrong"}, "invalid_credentials"),
                                  ({"Authorization":"Bearer expired"}, "expired_credentials")]:
                reply = self.gateway.get(self.mock, headers=headers)
                self.assertEqual(reply.status_code, 401)
                self.assertEqual(reply.json()["detail"], code)
                self.assertEqual(reply.headers["www-authenticate"], "Bearer")
        self.assertEqual(self.gateway.get(self.mock, headers={"Authorization":"bEaReR good"}).status_code, 200)

    def test_api_key_header_project_isolation_and_disable_without_restart(self):
        self.assertEqual(self.gateway.get(self.mock).status_code, 200)
        self.assertEqual(self.configure("api_key", header_name="X-Test-Key").status_code, 200)
        self.assertEqual(self.gateway.get(self.mock).status_code, 401)
        self.assertEqual(self.gateway.get(self.mock, headers={"x-test-key":"test-token"}).status_code, 200)
        self.assertEqual(self.gateway.get(f"/mock/{self.other.id}/hello").status_code, 200)
        self.assertEqual(self.configure("none", credentials=[]).status_code, 200)
        self.assertEqual(self.gateway.get(self.mock).status_code, 200)

    def test_private_storage_conflict_and_validation(self):
        original = self.console.get(self.url).json()
        result = self.configure()
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("test-token", result.text)
        self.assertNotIn("digest", result.text)
        path = self.project.directory / AUTH_PATH
        self.assertNotIn("test-token", path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        conflict = self.console.put(self.url, json={"expected_revision": original["revision"], "mode":"none"})
        self.assertEqual(conflict.status_code, 409)
        for fields in [{"mode":"bearer", "credentials":[]}, {"header_name":"Host"},
                       {"credentials":[{"name":"one", "value":"private invalid token"}]},
                       {"credentials":[{"name":"one", "value":"same"}, {"name":"two", "value":"same"}]},
                       {"credentials":[{"name":"one", "value":"test", "expires_at":"2026-01-01T00:00:00"}]}]:
            reply = self.configure(**fields)
            self.assertEqual(reply.status_code, 422, reply.text)
            self.assertNotIn("private invalid token", reply.text)
            self.assertEqual(reply.headers["cache-control"], "no-store")
        # Corrupt settings must fail closed rather than silently disable auth.
        path.write_text(json.dumps({"mode":"unknown"}))
        self.assertEqual(self.gateway.get(self.mock).status_code, 503)

    def test_duplicate_headers_and_missing_project(self):
        self.configure()
        reply = self.gateway.get(self.mock, headers=[("Authorization", "Bearer test-token"), ("Authorization", "Bearer wrong")])
        self.assertEqual(reply.status_code, 401)
        self.assertEqual(self.gateway.get("/mock/" + "a" * 32 + "/hello").status_code, 404)
