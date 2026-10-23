"""Per-project browser policy applies before preflight auth and to gateway errors."""

import hashlib
import io
import json
import zipfile
from unittest.mock import patch

from reqlica.projects.cors import CORS_PATH
from reqlica.projects.scenarios import get_scenarios
from reqlica.projects.transfer import export_project, import_project
from reqlica.projects.versions import code_directory
from reqlica.runtime.app import app as gateway
from test_scenarios import ScenarioFixture


class CorsTests(ScenarioFixture):
    def setUp(self):
        super().setUp()
        self.cors_url = f"/api/projects/{self.project.id}/cors"
        self.origin = "http://localhost:5173"

    def configure_cors(self, **settings):
        revision = self.console.get(self.cors_url).json()["revision"]
        return self.console.put(self.cors_url, json={"expected_revision":revision, "enabled":True,
            "allow_origins":[self.origin], **settings})

    def preflight(self, **headers):
        return self.gateway.options(self.mock + "/hello", headers={"Origin":self.origin,
            "Access-Control-Request-Method":"GET", "Access-Control-Request-Headers":"Authorization, X-Reqlica-Test-User", **headers})

    def test_disabled_default_and_dynamic_enable_disable(self):
        self.assertNotIn("access-control-allow-origin", self.gateway.get(self.mock + "/hello", headers={"Origin":self.origin}).headers)
        self.assertEqual(self.configure_cors().status_code, 200)
        reply = self.gateway.get(self.mock + "/hello", headers={"Origin":self.origin})
        self.assertEqual(reply.headers["access-control-allow-origin"], self.origin)
        self.assertIn("Origin", reply.headers["vary"])
        self.assertIn("X-Reqlica-Request-ID", reply.headers["access-control-expose-headers"])
        self.assertEqual(self.configure_cors(enabled=False).status_code, 200)
        self.assertNotIn("access-control-allow-origin", self.gateway.get(self.mock + "/hello", headers={"Origin":self.origin}).headers)

    def test_preflight_never_starts_worker_or_consumes_auth_or_counters(self):
        self.configure_cors(allow_credentials=True)
        auth = f"/api/projects/{self.project.id}/mock-auth"
        revision = self.console.get(auth).json()["revision"]
        self.console.put(auth, json={"expected_revision":revision,"mode":"bearer", "credentials":[{"name":"test","value":"token"}]})
        self.configure({"method":"GET", "path":"/hello", "response":"failure", "failure_count":1,
                        "rate_limit":{"requests":1,"window_seconds":60}})
        with patch.object(gateway.state.worker_manager, "acquire_request", side_effect=AssertionError("no worker")):
            reply = self.preflight()
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.headers["access-control-allow-origin"], self.origin)
        self.assertEqual(reply.headers["access-control-allow-credentials"], "true")
        self.assertEqual(get_scenarios(self.project.id)["rules"][0]["remaining_failures"], 1)
        reply = self.gateway.get(self.mock + "/hello", headers={"Origin":self.origin})
        self.assertEqual(reply.status_code, 401)
        self.assertEqual(reply.headers["access-control-allow-origin"], self.origin)

    def test_preflight_origin_method_header_rejections_and_scope(self):
        self.configure_cors(allow_methods=["GET"], allow_headers=["Authorization"])
        for overrides in ({"Origin":"https://wrong.test"}, {"Access-Control-Request-Method":"DELETE"},
                          {"Access-Control-Request-Headers":"X-Unknown"}):
            self.assertEqual(self.preflight(**overrides).status_code, 400)
        reply = self.preflight(Origin="https://wrong.test")
        self.assertNotIn("access-control-allow-origin", reply.headers)
        for url in (f"/mock/{self.other.id}/hello", "/mock/demo/hello", "/api/projects"):
            self.assertNotIn("access-control-allow-origin", self.gateway.get(url, headers={"Origin":self.origin}).headers)
        self.assertNotIn("access-control-allow-origin", self.console.get(self.cors_url, headers={"Origin":self.origin}).headers)

    def test_wildcard_and_credential_validation(self):
        self.assertEqual(self.configure_cors(allow_origins=["*"], allow_headers=["*"]).status_code, 200)
        self.assertEqual(self.preflight().headers["access-control-allow-origin"], "*")
        revision = self.console.get(self.cors_url).json()["revision"]
        for values in ({"allow_origins":["*"],"allow_credentials":True},
                       {"allow_headers":["*"],"allow_credentials":True},
                       {"expose_headers":["*"],"allow_credentials":True},
                       {"allow_origins":["http://example.com/path"]}, {"allow_origins":["null"]},
                       {"allow_origins":["https://example.com\r\ninvalid"]},
                       {"allow_methods":["get"]}, {"allow_headers":["bad\r\nHeader"]},
                       {"max_age":86401}, {"allow_origins":[]}):
            reply = self.configure_cors(**values)
            self.assertEqual(reply.status_code, 422, reply.text)
        reply = self.console.put(self.cors_url, json={"expected_revision":"f" * 64, "enabled":False})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(self.console.get(self.cors_url).json()["revision"], revision)

    def test_stopped_and_synthetic_errors_receive_cors_headers(self):
        self.configure_cors()
        self.configure({"method":"GET", "path":"/hello", "response":"failure", "rate_limit":{"requests":1,"window_seconds":60}})
        for status in (503, 429):
            reply = self.gateway.get(self.mock + "/hello", headers={"Origin":self.origin})
            self.assertEqual(reply.status_code, status)
            self.assertEqual(reply.headers["access-control-allow-origin"], self.origin)
        gateway.state.worker_manager.stop(self.project.id)
        reply = self.gateway.get(self.mock + "/hello", headers={"Origin":self.origin})
        self.assertEqual(reply.status_code, 503)
        self.assertEqual(reply.headers["access-control-allow-origin"], self.origin)
        self.assertEqual(self.preflight().status_code, 200)

    def test_worker_cannot_override_enabled_gateway_policy(self):
        (code_directory(self.project) / "endpoints/hello.py").write_text('from fastapi.responses import JSONResponse\ndef hello():\n    return JSONResponse({}, headers={"Access-Control-Allow-Origin":"*", "Access-Control-Allow-Credentials":"true", "Vary":"Accept"})\n')
        self.configure_cors()
        reply = self.gateway.get(self.mock + "/hello", headers={"Origin":"https://wrong.test"})
        self.assertEqual(reply.status_code, 200)
        self.assertNotIn("access-control-allow-origin", reply.headers)
        self.assertNotIn("access-control-allow-credentials", reply.headers)
        self.assertIn("Accept", reply.headers["vary"])
        self.assertIn("Origin", reply.headers["vary"])

    def test_persistence_origin_normalization_export_and_old_bundle_defaults(self):
        reply = self.configure_cors(allow_origins=["HTTP://LOCALHOST:80", "http://[::1]:5173"])
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["allow_origins"], ["http://localhost", "http://[::1]:5173"])
        archive = export_project(self.project.id)
        imported = import_project(archive, self.root / "copy", clone=True)["project"]
        self.assertEqual(self.console.get(f"/api/projects/{imported['id']}/cors").json()["allow_origins"], reply.json()["allow_origins"])
        # v1 archives created before CORS have no cors member in settings.
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            files = {name:source.read(name) for name in source.namelist()}
        settings = json.loads(files["settings.json"])
        settings.pop("cors")
        files["settings.json"] = json.dumps(settings).encode()
        metadata = json.loads(files["bundle.json"])
        metadata["files"]["settings.json"] = hashlib.sha256(files["settings.json"]).hexdigest()
        files["bundle.json"] = json.dumps(metadata).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as target:
            for name, raw in files.items():
                target.writestr(name, raw)
        old = import_project(buffer.getvalue(), self.root / "old", clone=True)["project"]
        self.assertFalse(self.console.get(f"/api/projects/{old['id']}/cors").json()["enabled"])

    def test_duplicate_origin_and_corrupt_settings_fail_closed(self):
        self.configure_cors()
        reply = self.gateway.get(self.mock + "/hello", headers=[("Origin",self.origin),("Origin","https://wrong.test")])
        self.assertEqual(reply.status_code, 400)
        self.assertNotIn("access-control-allow-origin", reply.headers)
        (self.project.directory / CORS_PATH).write_text('{invalid')
        self.assertEqual(self.preflight().status_code, 503)
        self.assertEqual(self.console.get(self.cors_url).status_code, 409)
        # Non-browser clients are unaffected by CORS configuration.
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
