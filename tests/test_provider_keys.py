"""Persistent private keys remain project/provider scoped and write-only."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation.credentials import get_key
from reqlica.generation.schemas import ProviderEndpoint
from reqlica.projects.store import create_project, registry_directory


class ProviderKeyTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {"REQLICA_HOME": str(self.directory / "home")}))
        self.project = create_project(self.directory / "project", "Project")
        self.client = self.enterContext(TestClient(create_app()))
        self.root = f"/api/projects/{self.project.id}/ai"

    def save(self, **endpoint):
        response = self.client.put(self.root + "/key", json={"provider": "groq", "api_key": "private-test-key", **endpoint})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"configured": True})
        self.assertNotIn("private-test-key", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_persists_with_private_permissions_outside_project_source(self):
        self.save()
        path = registry_directory().parent / "provider-keys" / f"{self.project.id}.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertFalse(path.is_relative_to(self.project.directory))
        with TestClient(create_app()) as restarted:
            result = restarted.post(self.root + "/key/status", json={"provider": "groq"})
            self.assertEqual(result.json(), {"configured": True})
        self.assertEqual(get_key(self.project.id, ProviderEndpoint()).get_secret_value(), "private-test-key")

    def test_isolated_by_project_provider_and_endpoint(self):
        self.save(provider="compatible", base_url="https://one.example/v1/")
        self.assertIsNone(get_key(self.project.id, ProviderEndpoint()))
        self.assertIsNone(get_key(self.project.id, ProviderEndpoint(provider="compatible", base_url="https://two.example/v1")))
        self.assertIsNotNone(get_key(self.project.id, ProviderEndpoint(provider="compatible", base_url="https://one.example/v1")))
        other = create_project(self.directory / "other", "Other")
        self.assertIsNone(get_key(other.id, ProviderEndpoint(provider="compatible", base_url="https://one.example/v1")))

    def test_replace_and_remove(self):
        self.save()
        self.save(api_key="replacement")
        self.assertEqual(get_key(self.project.id, ProviderEndpoint()).get_secret_value(), "replacement")
        response = self.client.request("DELETE", self.root + "/key", json={"provider": "groq"})
        self.assertEqual(response.json(), {"configured": False})
        self.assertIsNone(get_key(self.project.id, ProviderEndpoint()))

    def test_reveal_requires_explicit_request_and_stays_scoped(self):
        self.save()
        status = self.client.post(self.root + "/key/status", json={"provider": "groq"})
        self.assertNotIn("private-test-key", status.text)
        revealed = self.client.post(self.root + "/key/reveal", json={"provider": "groq"})
        self.assertEqual(revealed.status_code, 200)
        self.assertEqual(revealed.json(), {"api_key": "private-test-key"})
        self.assertEqual(revealed.headers["cache-control"], "no-store")
        self.assertEqual(self.client.post(self.root + "/key/reveal", json={"provider": "openai"}).status_code, 404)
        other = create_project(self.directory / "other", "Other")
        other_root = f"/api/projects/{other.id}/ai"
        self.assertEqual(self.client.post(other_root + "/key/reveal", json={"provider": "groq"}).status_code, 404)
        self.client.request("DELETE", self.root + "/key", json={"provider": "groq"})
        self.assertEqual(self.client.post(self.root + "/key/reveal", json={"provider": "groq"}).status_code, 404)

    def test_invalid_requests_never_echo_private_input(self):
        for payload in [
            {"provider": "invalid", "api_key": "private-test-key"},
            {"provider": "groq", "api_key": "private-test-key\ninvalid"},
            {"provider": "groq", "api_key": "x" * 9000},
            {"provider": "groq", "api_key": ""},
        ]:
            response = self.client.put(self.root + "/key", json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("private-test-key", response.text)
            self.assertNotIn("x" * 100, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_project_discovery_uses_saved_key_without_returning_it(self):
        self.save()
        with patch("reqlica.console.routes.provider_keys.discover_models", return_value={"models": []}) as discover:
            response = self.client.post(self.root + "/models", json={"provider": "groq"})
        self.assertEqual(response.json(), {"models": []})
        self.assertEqual(discover.call_args.args[1].get_secret_value(), "private-test-key")
        self.assertNotIn("private-test-key", response.text)

    def test_rejects_symlinked_key_file_without_reading_target(self):
        self.save()
        path = registry_directory().parent / "provider-keys" / f"{self.project.id}.json"
        path.unlink()
        target = self.directory / "outside.json"
        target.write_text('{"private-test-key":"secret"}')
        path.symlink_to(target)
        response = self.client.post(self.root + "/key/status", json={"provider": "groq"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private-test-key", response.text)
