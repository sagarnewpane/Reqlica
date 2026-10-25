"""Import handoff and revision-pinned exports; no paid provider calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import yaml
from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation import agent
from reqlica.generation.schemas import AgentRequest
from reqlica.generation.specifications import Specification, prepare
from reqlica.projects import drafts
from reqlica.projects.store import create_project

CONTRACT = {"openapi": "3.1.0", "info": {"title": "Events", "version": "1"}, "paths": {
    "/events": {"get": {"responses": {"200": {"description": "Events"}}}},
    "/other": {"post": {"responses": {"201": {"description": "Created"}}}}}}


class SpecificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, REQLICA_HOME=str(self.root / "home")))
        self.project = create_project(self.root / "api", "API")
        self.client = self.enterContext(TestClient(create_app()))
        self.base = f"/api/projects/{self.project.id}"

    def test_yaml_preview_selection_and_limits(self):
        spec = Specification(filename="api.yaml", content=yaml.safe_dump(CONTRACT), operations=["GET /events"])
        prepared = prepare(spec)
        self.assertEqual(list(json.loads(prepared["requirements"])["paths"]), ["/events"])
        self.assertEqual(prepared["operations"], ["GET /events", "POST /other"])
        response = self.client.post(self.base + "/specifications/preview", json=spec.model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("requirements", response.json())
        with self.assertRaises(ValueError):
            prepare(Specification(filename="bad.json", content="{oops"))
        with self.assertRaises(ValueError):
            prepare(Specification(filename="large.txt", content="x" * 24577))

    def test_import_persists_and_invalidates_checks_and_other_chats(self):
        draft = drafts.create_draft(self.project.id)
        old = agent.create_run(self.project.id, uuid4().hex, AgentRequest(
            model="test", message="Inspect", draft_id=draft["id"], expected_revision=draft["revision"]))
        raw_old = agent._read(agent._path(self.project.id, old["id"]))
        with patch("reqlica.generation.agent.provider.complete") as model:
            imported = agent.create_run(self.project.id, uuid4().hex, AgentRequest(
                model="test", message="Implement", draft_id=draft["id"], expected_revision=draft["revision"],
                specification=Specification(filename="api.json", content=json.dumps(CONTRACT))))
            model.assert_not_called()
        saved = drafts.inspect_draft(self.project.id, draft["id"])
        self.assertEqual(json.loads(saved["generation"]["requirements"]), CONTRACT)
        self.assertIsNone(saved["test"])
        with self.assertRaisesRegex(ValueError, "requirements changed"):
            agent._check_scope(self.project.id, raw_old)
        followup = agent.create_run(self.project.id, uuid4().hex, AgentRequest(
            model="test", message="Continue", draft_id=draft["id"], expected_revision=draft["revision"]))
        self.assertEqual(followup["specification"]["filename"], "api.json")
        self.assertNotIn("requirements", imported["specification"])
        # An idempotent create retry must not replace the checkpoint or reimport.
        request = agent._read(agent._path(self.project.id, imported["id"]))["request"]
        self.assertEqual(agent.create_run(self.project.id, imported["id"], AgentRequest.model_validate(request))["id"], imported["id"])

    def test_download_json_yaml_and_revision_conflict(self):
        document = {**CONTRACT, "x-reqlica-revision": "a" * 64}
        result = {"revision": "a" * 64, "schema": document}
        with patch("reqlica.projects.schemas.get_api_schema", return_value=result):
            for format in ("json", "yaml"):
                response = self.client.get(self.base + f"/schema-export?revision={'a'*64}&format={format}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("attachment;", response.headers["content-disposition"])
                parsed = yaml.safe_load(response.text)
                self.assertEqual(parsed["paths"], CONTRACT["paths"])
                self.assertTrue(parsed["servers"][0]["url"].endswith(f"/mock/{self.project.id}"))
                self.assertTrue(parsed["servers"][0]["url"].startswith("http://"))
            response = self.client.get(self.base + f"/schema-export?revision={'b'*64}")
            self.assertEqual(response.status_code, 409)

    def test_new_ai_draft_receives_original_contract(self):
        from test_agent import tool_message
        run_id = uuid4().hex
        self.client.put(self.base + f"/agent-runs/{run_id}", json={
            "message": "Implement attached API", "model": "test",
            "specification": {"filename": "api.json", "content": json.dumps(CONTRACT)}}).raise_for_status()
        with patch("reqlica.generation.agent.provider.complete", return_value=(tool_message("create_draft", {"name": "Imported API"}), None)):
            for _ in range(2):
                response = self.client.post(self.base + f"/agent-runs/{run_id}/step", headers={"X-Reqlica-Groq-Key": "fixture-only"})
                self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        self.assertIsNotNone(run["draft_id"], run)
        saved = drafts.inspect_draft(self.project.id, run["draft_id"])
        self.assertEqual(json.loads(saved["generation"]["requirements"]), CONTRACT)
