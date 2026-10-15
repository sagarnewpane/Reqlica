"""Generated contracts are revision-linked, read-only, and isolated from live code."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.projects import drafts
from reqlica.projects.schemas import get_api_schema, serving_document
from reqlica.projects.store import Endpoint, create_project, get_project
from reqlica.projects.versions import content_digest
from reqlica.runtime.app import app as gateway_app
from reqlica.runtime.worker import WorkerManager
from reqlica.validation.openapi import OPENAPI_LIMIT

SOURCE = """from fastapi import Header
from pydantic import BaseModel, Field

class GreetingInput(BaseModel):
    name: str = Field(min_length=1, description="Person to greet", examples=["Sagar"])
    times: int = Field(default=1, ge=1, le=3)

class GreetingOutput(BaseModel):
    message: str
    count: int

def greet(greeting_id: int, body: GreetingInput, uppercase: bool = False,
          trace: str | None = Header(default=None)) -> GreetingOutput:
    message = f"Hello, {body.name}"
    return GreetingOutput(message=message.upper() if uppercase else message, count=body.times)
"""


class SchemaTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "project", "Greeting API")
        self.draft = drafts.create_draft(self.project.id)
        self.write("endpoints/greeting.py", SOURCE)
        self.route("POST", "/greet/{greeting_id:int}", "endpoints/greeting.py:greet")
        self.client = self.enterContext(TestClient(create_app()))
        self.base = f"/api/projects/{self.project.id}/drafts/{self.draft['id']}"

    def write(self, path, content):
        self.draft = drafts.write_draft_file(
            self.project.id, self.draft["id"], path, content, self.draft["revision"]
        )

    def route(self, method, path, handler):
        self.draft = drafts.upsert_endpoint(
            self.project.id,
            self.draft["id"],
            Endpoint(method=method, path=path, handler=handler),
            self.draft["revision"],
        )

    def validate(self):
        result = drafts.validate_draft(
            self.project.id, self.draft["id"], self.draft["revision"]
        )
        self.assertEqual(result["status"], "passed", result)
        return result

    def selected(self):
        return get_api_schema(
            self.project.id, self.draft["id"], "POST", "/greet/{greeting_id:int}"
        )

    def test_typed_request_response_parameters_and_constraints_are_generated(self):
        result = self.validate()
        selected = self.selected()
        self.assertEqual(selected["contract_status"], "complete", selected["warnings"])
        document = selected["schema"]
        operation = document["paths"]["/greet/{greeting_id}"]["post"]
        self.assertTrue(operation["requestBody"]["required"])
        self.assertTrue(
            operation["requestBody"]["content"]["application/json"]["schema"][
                "$ref"
            ].endswith("GreetingInput")
        )
        model = document["components"]["schemas"]["GreetingInput"]
        self.assertEqual(model["required"], ["name"])
        self.assertEqual(model["properties"]["name"]["minLength"], 1)
        self.assertEqual(model["properties"]["name"]["examples"], ["Sagar"])
        self.assertEqual(model["properties"]["times"]["default"], 1)
        self.assertEqual(model["properties"]["times"]["maximum"], 3)
        parameters = {
            (item["in"], item["name"]): item for item in operation["parameters"]
        }
        self.assertTrue(parameters["path", "greeting_id"]["required"])
        self.assertEqual(parameters["query", "uppercase"]["schema"]["type"], "boolean")
        self.assertIn(("header", "trace"), parameters)
        self.assertIn(
            "GreetingOutput",
            operation["responses"]["200"]["content"]["application/json"]["schema"][
                "$ref"
            ],
        )
        self.assertTrue(
            result["schema"]["artifact"].startswith(".reqlica/openapi/drafts/")
        )

    def test_artifacts_are_pure_openapi_and_do_not_change_draft_revision(self):
        directory = drafts.draft_directory(self.project, self.draft["id"])
        before = content_digest(directory)
        result = self.validate()
        artifact = self.project.directory / result["schema"]["artifact"]
        document = json.loads(artifact.read_text())
        self.assertTrue(document["openapi"].startswith("3."))
        self.assertEqual(document["x-reqlica-revision"], before)
        self.assertEqual(content_digest(directory), before)
        self.assertFalse((directory / "openapi.json").exists())
        self.assertNotIn(
            result["schema"]["artifact"],
            drafts.inspect_draft(self.project.id, self.draft["id"])["files"],
        )

    def test_edits_require_revalidation_and_keep_revision_history_separate(self):
        old = self.validate()
        old_path = self.project.directory / old["schema"]["artifact"]
        self.write("endpoints/greeting.py", SOURCE.replace("name", "full_name"))
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.selected()
        self.assertEqual(self.client.get(self.base + "/openapi.json").status_code, 409)
        current = self.validate()
        self.assertNotEqual(old["schema"]["artifact"], current["schema"]["artifact"])
        self.assertTrue(old_path.exists())
        self.assertIn(
            "full_name",
            self.selected()["schema"]["components"]["schemas"]["GreetingInput"][
                "properties"
            ],
        )

    def test_free_form_and_raw_request_inputs_warn_without_inventing_fields(self):
        self.write(
            "endpoints/free.py",
            "from fastapi import Request\n\ndef free(body: dict) -> dict:\n    return body\n\nasync def raw(request: Request):\n    return await request.json()\n",
        )
        self.route("POST", "/free", "endpoints/free.py:free")
        self.route("POST", "/raw", "endpoints/free.py:raw")
        self.validate()
        free = get_api_schema(self.project.id, self.draft["id"], "POST", "/free")
        self.assertEqual(free["contract_status"], "incomplete")
        self.assertIn("request.body", {item["location"] for item in free["warnings"]})
        body = free["schema"]["paths"]["/free"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        self.assertNotIn("name", body.get("properties", {}))
        raw = get_api_schema(self.project.id, self.draft["id"], "POST", "/raw")
        self.assertIn("request", {item["location"] for item in raw["warnings"]})

    def test_schema_reads_never_execute_code_or_clear_exact_revision_approval(self):
        self.validate()
        args = self.project.id, self.draft["id"], self.draft["revision"]
        self.assertEqual(drafts.test_draft(*args)["status"], "passed")
        drafts.approve_draft(*args)
        with patch(
            "reqlica.validation.checks._execute",
            side_effect=AssertionError("must not execute"),
        ):
            self.selected()
            self.client.get(self.base + "/openapi.json").raise_for_status()
            self.client.get(self.base + "/docs").raise_for_status()
        self.assertEqual(
            drafts.inspect_draft(*args[:2])["approval"], {"revision": args[2]}
        )

    def test_cache_tampering_and_symlink_traversal_are_rejected(self):
        validation = self.validate()
        path = self.project.directory / validation["schema"]["artifact"]
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "changed or is incomplete"):
            self.selected()
        self.validate()
        outside = self.root / "schema.json"
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.selected()

    def test_oversized_or_missing_generated_schema_fails_validation(self):
        def oversized(job, workspace):
            Path(job["openapi_result"]).write_bytes(b"x" * (OPENAPI_LIMIT + 1))
            return {"status": "passed", "output": "", "tests_run": 0, "tests_passed": 0}

        with patch("reqlica.validation.checks._execute", side_effect=oversized):
            result = drafts.validate_draft(
                self.project.id, self.draft["id"], self.draft["revision"]
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("size limit", result["output"])
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.selected()

    def test_routes_that_collide_after_openapi_path_normalization_fail_validation(self):
        self.route("POST", "/greet/{greeting_id:str}", "endpoints/greeting.py:greet")
        result = drafts.validate_draft(
            self.project.id, self.draft["id"], self.draft["revision"]
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("Routes collide in OpenAPI", result["output"])
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.selected()

    def test_invalid_metadata_and_unknown_endpoint_selectors_are_rejected(self):
        validation = self.validate()
        for method, path in (
            ("GET", None),
            (None, "/hello"),
            ("UNKNOWN", "/hello"),
            ("GET", "https://example.com/hello"),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(ValueError):
                get_api_schema(self.project.id, self.draft["id"], method, path)
        with self.assertRaises(FileNotFoundError):
            get_api_schema(self.project.id, self.draft["id"], "GET", "/missing")
        artifact = self.project.directory / validation["schema"]["artifact"]
        artifact.with_suffix(".metadata.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "Invalid schema artifact"):
            self.selected()

    def test_swagger_uses_preview_and_revision_pin_not_live_gateway(self):
        self.validate()
        response = self.client.get(self.base + "/openapi.json")
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()
        self.assertEqual(document["servers"], [{"url": self.base + "/preview"}])
        operation = document["paths"]["/greet/{greeting_id}"]["post"]
        pin = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "X-Reqlica-Expected-Revision"
        )
        self.assertEqual(pin["schema"]["default"], self.draft["revision"])
        page = self.client.get(self.base + "/docs")
        self.assertEqual(page.status_code, 200)
        self.assertIn("swagger-ui", page.text)
        self.assertIn("revision=" + self.draft["revision"], page.text)
        revision = self.draft["revision"]
        response = self.client.post(
            self.base + "/preview/greet/7?uppercase=true",
            json={"name": "Sagar"},
            headers={"X-Reqlica-Expected-Revision": revision},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"message": "HELLO, SAGAR", "count": 1})
        self.write("notes.txt", "changed")
        response = self.client.post(
            self.base + "/preview/greet/7",
            json={"name": "Sagar"},
            headers={"X-Reqlica-Expected-Revision": revision},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.client.get(
                self.base + "/openapi.json?revision=" + revision
            ).status_code,
            409,
        )
        response = self.client.get(
            self.base + "/preview/hello",
            headers={"X-Reqlica-Expected-Revision": "placeholder"},
        )
        self.assertEqual(response.status_code, 400)

    def test_serving_never_preserves_operation_or_path_server_overrides(self):
        self.validate()
        result = self.selected()
        item = result["schema"]["paths"]["/greet/{greeting_id}"]
        item["servers"] = [{"url": "https://live.example"}]
        item["post"]["servers"] = [{"url": "https://live.example"}]
        original = copy.deepcopy(result)
        served = serving_document(result, "/preview", preview=True)
        self.assertNotIn("servers", served["paths"]["/greet/{greeting_id}"])
        self.assertNotIn("servers", served["paths"]["/greet/{greeting_id}"]["post"])
        self.assertEqual(served["servers"], [{"url": "/preview"}])
        self.assertEqual(result, original)

    def test_published_schema_is_promoted_only_at_activation_and_stays_separate(self):
        self.validate()
        with self.assertRaisesRegex(ValueError, "No generated schema"):
            get_api_schema(self.project.id)
        args = self.project.id, self.draft["id"], self.draft["revision"]
        self.assertEqual(drafts.test_draft(*args)["status"], "passed")
        drafts.approve_draft(*args)
        manager = WorkerManager()
        try:
            manager.activate(*args)
        finally:
            manager.close()
        active = get_project(self.project.id)
        published = get_api_schema(
            self.project.id, method="POST", path="/greet/{greeting_id}"
        )
        self.assertEqual(published["version_id"], active.active_version)
        self.assertNotIn("x-reqlica-draft-id", published["schema"])
        self.assertIn(
            "name",
            published["schema"]["components"]["schemas"]["GreetingInput"]["properties"],
        )
        self.draft = drafts.create_draft(self.project.id)
        self.write("endpoints/greeting.py", SOURCE.replace("name", "full_name"))
        self.validate()
        self.assertIn(
            "full_name",
            self.selected()["schema"]["components"]["schemas"]["GreetingInput"][
                "properties"
            ],
        )
        self.assertIn(
            "name",
            get_api_schema(self.project.id)["schema"]["components"]["schemas"][
                "GreetingInput"
            ]["properties"],
        )
        with TestClient(gateway_app) as client:
            response = client.get(f"/api/projects/{self.project.id}/openapi.json")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                response.json()["servers"], [{"url": f"/mock/{self.project.id}"}]
            )
            self.assertEqual(
                client.get(f"/api/projects/{self.project.id}/docs").status_code, 200
            )
            self.assertEqual(gateway_app.state.worker_manager.workers, {})
