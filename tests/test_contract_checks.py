"""Contract checks must generalize across domains, without trusting generated tests."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reqlica.generation.repair import repair_draft
from reqlica.generation.schemas import RepairRequest
from reqlica.projects import drafts
from reqlica.projects.store import Endpoint, create_project
from reqlica.projects.versions import atomic_write_json
from reqlica.validation.contracts import ContractChecks, parse_contract


def specification(path="/reservations", schema=None):
    return {
        "openapi": "3.0.3",
        "info": {"title": "Reservations", "version": "1"},
        "paths": {
            path: {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": schema or {"type": "object", "required": ["label"], "properties": {"label": {"type": "string", "minLength": 1, "maxLength": 128}}}}}},
                    "responses": {"200": {"description": "Accepted", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Receipt"}}}}},
                }
            }
        },
        "components": {"schemas": {"Receipt": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}, "additionalProperties": False}}},
    }


class ContractUnitTests(unittest.TestCase):
    def test_complete_json_yaml_fenced_and_prose(self):
        document = specification()
        self.assertEqual(parse_contract(json.dumps(document)), document)
        self.assertEqual(parse_contract("```json\n" + json.dumps(document) + "\n```"), document)
        auth = parse_contract((Path(__file__).parent / "auth-jwt.openapi.yaml").read_text())
        self.assertEqual(auth["openapi"], "3.0.3")
        self.assertIsNone(parse_contract("Build reservations with labels."))

    def test_invalid_duplicate_recursive_or_oversized_contract_is_not_ignored(self):
        for text in (
            "openapi: 3.0.3\nopenapi: 3.1.0",
            "openapi: [broken",
            "openapi: 3.0.3\npaths: &loop {again: *loop}",
            "openapi: 2.0\npaths: {}",
            "openapi: 3.0.3\nextra: " + "x" * 131072,
            "openapi: 3.0.3\ninfo: {title: bad, version: 1}\npaths: {}",
        ):
            with self.subTest(text=text[:80]), self.assertRaises(ValueError):
                parse_contract(text)

    def compare(self, expected, actual):
        original = specification(schema=expected)
        generated = specification(schema=actual)
        checks = ContractChecks(original)
        checks.compare_inputs(generated)
        return checks

    def test_dynamic_string_number_array_enum_and_missing_boundaries(self):
        for expected, actual, keyword in (
            ({"type": "string", "minLength": 1}, {"type": "string", "minLength": 8}, "minLength"),
            ({"type": "string"}, {"type": "string", "minLength": 1}, "minLength"),
            ({"type": "integer", "minimum": -5}, {"type": "integer", "minimum": 0}, "minimum"),
            ({"type": "number"}, {"type": "number", "maximum": 10}, "maximum"),
            ({"type": "array", "maxItems": 50}, {"type": "array", "maxItems": 20}, "maxItems"),
            ({"type": "string", "enum": ["new", "old"]}, {"type": "string", "enum": ["new"]}, "enum"),
            ({"type": "string", "nullable": True}, {"type": "string"}, "null"),
        ):
            with self.subTest(keyword=keyword):
                checks = self.compare(expected, actual)
                self.assertIn(keyword, json.dumps(checks.failures))

    def test_separate_auth_models_are_checked_by_contract_not_route_name(self):
        original = parse_contract((Path(__file__).parent / "auth-jwt.openapi.yaml").read_text())
        generated = copy.deepcopy(original)
        generated["components"]["schemas"]["LoginRequest"]["properties"]["password"]["minLength"] = 8
        checks = ContractChecks(original)
        checks.compare_inputs(generated)
        self.assertEqual(len(checks.failures), 1)
        self.assertIn("POST /auth/login", checks.failures[0]["test"])
        self.assertIn("body.password", checks.failures[0]["output"])
        # Same rule, unrelated domain and field names.
        checks = self.compare({"type": "string", "minLength": 1}, {"type": "string", "minLength": 8})
        self.assertIn("/reservations", checks.failures[0]["test"])

    def test_nested_fields_required_and_arrays(self):
        expected = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string", "maxLength": 100}}}}
        actual = copy.deepcopy(expected)
        actual["required"] = ["items"]
        actual["properties"]["items"]["items"]["maxLength"] = 20
        checks = self.compare(expected, actual)
        self.assertIn("adds required", json.dumps(checks.failures))
        self.assertIn("body.items[]", json.dumps(checks.failures))

    def test_widened_input_is_not_misreported_as_narrowed(self):
        self.assertFalse(self.compare({"type": "integer", "minimum": 3}, {"type": "integer", "minimum": 0}).failures)

    def test_composed_inputs_and_external_refs_are_explicit_gaps(self):
        checks = self.compare({"anyOf": [{"type": "string"}, {"type": "null"}]}, {"type": "string"})
        self.assertFalse(checks.failures)
        self.assertIn("Composed input", json.dumps(checks.warnings))
        document = specification()
        document["components"]["schemas"]["Receipt"] = {"$ref": "https://example.com/private.json"}
        checks = ContractChecks(document)
        self.assertIsNone(checks.validator)
        self.assertIn("External references", json.dumps(checks.report()))

    def test_query_header_parameters_and_security_are_contract_driven(self):
        original = specification()
        operation = original["paths"]["/reservations"]["post"]
        operation["parameters"] = [{"in": "query", "name": "limit", "schema": {"type": "integer", "maximum": 100}}]
        operation["security"] = [{"bearer": []}]
        original["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
        generated = copy.deepcopy(original)
        parameters = generated["paths"]["/reservations"]["post"]["parameters"]
        parameters[0]["schema"]["maximum"] = 20
        parameters.append({"in": "header", "name": "Authorization", "required": True, "schema": {"type": "string"}})
        checks = ContractChecks(original)
        checks.compare_inputs(generated)
        self.assertEqual(len(checks.failures), 1, checks.failures)
        self.assertIn("query.limit", checks.failures[0]["output"])

    def observe(self, body, status=200, document=None):
        checks = ContractChecks(document or specification("/reservations/{item_id}"))
        app = FastAPI()
        from fastapi.responses import JSONResponse

        @app.post("/reservations/{item_id}")
        def handler(item_id: str):
            return JSONResponse(body, status_code=status)

        original_send = TestClient.send
        with checks.monitor(), TestClient(app) as client:
            client.post("/reservations/abc")
        self.assertIs(TestClient.send, original_send)
        return checks

    def test_observed_responses_use_original_refs_status_and_shapes(self):
        checks = self.observe({"code": "OK"})
        self.assertFalse(checks.failures, checks.failures)
        self.assertEqual(checks.report()["responses_checked"], 1)
        self.assertEqual(checks.report()["routes_unexercised"], [])
        for body in ({"detail": {"code": "ERROR"}}, {"code": 42}):
            checks = self.observe(body)
            self.assertTrue(checks.failures)
        self.assertIn("Undeclared response status 201", self.observe({"code": "OK"}, 201).failures[0]["output"])

    def test_values_do_not_leak_in_contract_failures(self):
        secret = "must-not-leak-sensitive-value"
        checks = self.observe({"code": {"private": secret}})
        self.assertNotIn(secret, json.dumps(checks.failures))

    def test_nullable_30_native_31_and_writeonly_response_fields(self):
        for version in ("3.0.3", "3.1.0"):
            document = specification("/reservations/{item_id}")
            document["openapi"] = version
            receipt = document["components"]["schemas"]["Receipt"]
            receipt["required"].append("secret")
            receipt["properties"]["secret"] = {"type": "string", "writeOnly": True}
            receipt["properties"]["code"] = {"type": "string", "nullable": True} if version == "3.0.3" else {"type": ["string", "null"]}
            checks = self.observe({"code": None}, document=document)
            self.assertFalse(checks.failures, checks.failures)

    def test_unexercised_routes_and_direct_calls_are_not_counted_as_conformance(self):
        report = ContractChecks(specification()).report()
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["responses_checked"], 0)
        self.assertEqual(report["routes_unexercised"], ["POST /reservations"])

    def test_inline_nullable_and_schema_keyword_property_names(self):
        document = specification("/reservations/{item_id}")
        entry = document["paths"]["/reservations/{item_id}"]["post"]["responses"]["200"]["content"]["application/json"]
        entry["schema"] = {"type": "object", "required": ["type", "nullable", "properties", "required"], "properties": {
            "type": {"type": "string", "nullable": True},
            "nullable": {"type": "boolean"},
            "properties": {"type": "string"},
            "required": {"type": "string"},
        }}
        checks = self.observe({"type": None, "nullable": True, "properties": "data", "required": "data"}, document=document)
        self.assertFalse(checks.failures, checks.failures)
        self.assertEqual(checks.responses_checked, 1)

    def test_declared_header_values_are_checked_without_leaking_values(self):
        document = specification("/reservations/{item_id}")
        response = document["paths"]["/reservations/{item_id}"]["post"]["responses"]["200"]
        response["headers"] = {"Content-Type": {"schema": {"type": "string", "enum": ["text/plain"]}}}
        checks = self.observe({"code": "OK"}, document=document)
        self.assertIn("header Content-Type violates enum", json.dumps(checks.failures))


SOURCE = '''from fastapi import FastAPI
from pydantic import BaseModel, Field
class Reservation(BaseModel):
    label: str = Field(min_length=8, max_length=128)
def reserve(body: Reservation) -> dict[str, str]:
    return {"code": "OK"}
'''
HTTP_TEST = '''import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from endpoints.reserve import reserve
class ReservationTests(unittest.TestCase):
    def test_happy_path(self):
        app = FastAPI()
        app.add_api_route("/reservations", reserve, methods=["POST"])
        with TestClient(app) as client:
            self.assertEqual(client.post("/reservations", json={"label":"long-enough"}).status_code, 200)
'''


class ContractPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home"), "TZ": "UTC"})
        environment.start()
        self.addCleanup(environment.stop)
        self.project = create_project(self.root / "project", "Reservations")
        self.draft = drafts.create_draft(self.project.id)
        self.write("endpoints/reserve.py", SOURCE)
        self.write("tests/test_reservations.py", HTTP_TEST)
        self.draft = drafts.upsert_endpoint(self.project.id, self.draft["id"], Endpoint(method="POST", path="/reservations", handler="endpoints/reserve.py:reserve"), self.draft["revision"])
        self.save_requirements(json.dumps(specification()))

    def save_requirements(self, requirements):
        state = drafts._state(self.project, self.draft["id"])
        state["generation"] = {"requirements": requirements, "clarification": None}
        atomic_write_json(drafts._state_path(self.project, self.draft["id"]), state)

    def write(self, path, content):
        self.draft = drafts.write_draft_file(self.project.id, self.draft["id"], path, content, self.draft["revision"])

    def arguments(self):
        return self.project.id, self.draft["id"], self.draft["revision"]

    def test_weak_generated_tests_cannot_hide_input_or_response_drift(self):
        self.assertEqual(drafts.validate_draft(*self.arguments())["status"], "passed")
        result = drafts.test_draft(*self.arguments())
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["tests_passed"], result["tests_run"])
        self.assertIn("body.label", json.dumps(result["failures"]))
        with self.assertRaisesRegex(ValueError, "must pass"):
            drafts.approve_draft(*self.arguments())
        self.write("endpoints/reserve.py", SOURCE.replace("min_length=8", "min_length=1").replace('{"code": "OK"}', '{"detail": "wrong envelope"}'))
        result = drafts.test_draft(*self.arguments())
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("violates required", json.dumps(result["failures"]))

    def test_repair_consumes_independent_failures_without_changing_tests(self):
        drafts.test_draft(*self.arguments())
        directory = drafts.draft_directory(self.project, self.draft["id"])
        before = (directory / "tests/test_reservations.py").read_bytes()
        plan = {"summary": "Preserve original minimum label length", "blockers": [], "files": [{"path": "endpoints/reserve.py", "content": SOURCE.replace("min_length=8", "min_length=1")}]}
        with patch("reqlica.generation.provider.complete", return_value=(json.dumps(plan), None)) as provider:
            result = repair_draft(self.project.id, self.draft["id"], RepairRequest(expected_revision=self.draft["revision"], confirm=True))
        self.assertEqual(result["status"], "repaired", result)
        self.assertIn("body.label", provider.call_args.args[0][1]["content"])
        self.assertEqual((directory / "tests/test_reservations.py").read_bytes(), before)
        self.assertIsNone(result["draft"]["approval"])
        self.assertEqual(result["test"]["contract_checks"]["responses_checked"], 1)

    def test_environment_matrix_catches_time_bug_outside_auth_with_fresh_imports(self):
        self.save_requirements("Schedule reservations with a fifteen-minute expiry.")
        self.write("endpoints/reserve.py", SOURCE.replace("min_length=8", "min_length=1"))
        self.write("tests/test_reservations.py", '''import datetime
import unittest
class SchedulingTests(unittest.TestCase):
    def test_new_reservation_is_not_expired(self):
        # Deliberately buggy time conversion, independent of authentication.
        created = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        expires = created.timestamp() + 900
        self.assertGreater(expires, datetime.datetime.now(datetime.UTC).timestamp())
''')
        result = drafts.test_draft(*self.arguments())
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["environment_checks"][0]["status"], "passed")
        self.assertEqual(result["environment_checks"][1]["status"], "failed")
        self.assertIn("Asia/Kathmandu", json.dumps(result["failures"]))
        self.assertEqual(os.environ["TZ"], "UTC")
        self.write("tests/test_reservations.py", (drafts.draft_directory(self.project, self.draft["id"]) / "tests/test_reservations.py").read_text().replace(".replace(tzinfo=None)", ""))
        result = drafts.test_draft(*self.arguments())
        self.assertEqual(result["status"], "passed", result)

    def test_every_environment_variant_starts_from_identical_disposable_data(self):
        import sqlite3

        database = self.project.directory / "data.sqlite"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE markers(value TEXT)")
        self.save_requirements("Append one reservation marker.")
        self.write("tests/test_reservations.py", '''import unittest
from reqlica.runtime.sdk import transaction
class StateTests(unittest.TestCase):
    def test_single_run(self):
        with transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM markers").fetchone()[0], 0)
            db.execute("INSERT INTO markers VALUES ('isolated')")
''')
        result = drafts.test_draft(*self.arguments())
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(all(item["status"] == "passed" for item in result["environment_checks"]))
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM markers").fetchone()[0], 0)

    def test_invalid_original_contract_blocks_even_when_tests_pass(self):
        self.save_requirements("openapi: [broken")
        result = drafts.test_draft(*self.arguments())
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("Original OpenAPI is invalid", json.dumps(result))
