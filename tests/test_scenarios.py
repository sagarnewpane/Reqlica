"""Exercise scenario routing, persistence, concurrency and gateway enforcement."""

import asyncio
import json
import multiprocessing
import os
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.projects.scenarios import SCENARIO_PATH, ScenarioWrite, consume_scenario, get_scenarios
from reqlica.projects.store import Manifest, create_project
from reqlica.projects.versions import code_directory
from reqlica.runtime.app import app as gateway, proxy


def consume_in_process(directory):
    manifest = Manifest(endpoints=[{"method":"GET", "path":"/hello", "handler":"endpoints/hello.py:hello"}])
    result = consume_scenario(Path(directory), "GET", "/hello", manifest)
    return result["response"]


class ScenarioFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, REQLICA_HOME=str(self.root / "home")))
        self.project = create_project(self.root / "api")
        self.other = create_project(self.root / "other")
        code = code_directory(self.project)
        (code / "endpoints/items.py").write_text('def item(item_id: str):\n    return {"id": item_id}\n')
        manifest = json.loads((code / "manifest.json").read_text())
        manifest["endpoints"] += [
            {"method":"GET", "path":"/items/static", "handler":"endpoints/hello.py:hello"},
            {"method":"GET", "path":"/items/{item_id}", "handler":"endpoints/items.py:item"},
            {"method":"HEAD", "path":"/hello", "handler":"endpoints/hello.py:hello"},
        ]
        (code / "manifest.json").write_text(json.dumps(manifest))
        self.manifest = Manifest.model_validate(manifest)
        self.console = self.enterContext(TestClient(create_app()))
        self.gateway = self.enterContext(TestClient(gateway))
        self.url = f"/api/projects/{self.project.id}/scenarios"
        self.mock = f"/mock/{self.project.id}"

    def configure(self, *rules):
        state = self.console.get(self.url).json()
        reply = self.console.put(self.url, json={"expected_revision":state["revision"], "rules":list(rules)})
        self.assertEqual(reply.status_code, 200, reply.text)
        return reply.json()


class ScenarioTests(ScenarioFixture):
    def test_next_n_failures_then_forward_and_persist(self):
        state = self.configure({"method":"GET", "path":"/hello", "response":"failure", "failure_count":2,
                                "status_code":502, "body":{"error":"planned"}})
        for remaining in (1, 0):
            reply = self.gateway.get(self.mock + "/hello?ignored=yes")
            self.assertEqual(reply.status_code, 502)
            self.assertEqual(reply.json(), {"error":"planned"})
            saved = get_scenarios(self.project.id)
            self.assertEqual(saved["rules"][0]["remaining_failures"], remaining)
            self.assertEqual(saved["revision"], state["revision"])
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        self.assertEqual(self.gateway.get(f"/mock/{self.other.id}/hello").status_code, 200)
        self.assertEqual(self.console.get(self.url).headers["cache-control"], "no-store")

    def test_route_patterns_precedence_empty_and_disable(self):
        self.configure({"method":"GET", "path":"/items/{item_id}", "response":"empty"})
        reply = self.gateway.get(self.mock + "/items/123")
        self.assertEqual(reply.json(), [])
        self.assertEqual(reply.headers["x-reqlica-scenario"], "empty")
        self.assertIn("message", self.gateway.get(self.mock + "/items/static").json())
        self.assertEqual(self.gateway.post(self.mock + "/items/123").status_code, 405)
        self.assertEqual(self.gateway.get(self.mock + "/unknown").status_code, 404)
        self.configure()
        self.assertEqual(self.gateway.get(self.mock + "/items/123").json(), {"id":"123"})

    def test_authentication_precedes_counter_and_head_has_no_body(self):
        self.configure({"method":"HEAD", "path":"/hello", "response":"failure", "failure_count":1})
        auth_url = f"/api/projects/{self.project.id}/mock-auth"
        revision = self.console.get(auth_url).json()["revision"]
        self.console.put(auth_url, json={"expected_revision":revision,"mode":"bearer", "credentials":[{"name":"tester", "value":"token"}]})
        self.assertEqual(self.gateway.head(self.mock + "/hello").status_code, 401)
        self.assertEqual(get_scenarios(self.project.id)["rules"][0]["remaining_failures"], 1)
        reply = self.gateway.head(self.mock + "/hello", headers={"Authorization":"Bearer token"})
        self.assertEqual(reply.status_code, 503)
        self.assertEqual(reply.content, b"")

    def test_delay_is_awaited_for_failures_and_passthrough(self):
        real_sleep = asyncio.sleep
        observed = []
        async def record_sleep(seconds):
            if seconds == 0.125:
                observed.append(seconds)
                return
            await real_sleep(seconds)
        # Warm startup so only scenario delays are observed.
        self.gateway.get(self.mock + "/hello")
        with patch("reqlica.runtime.app.asyncio.sleep", side_effect=record_sleep):
            for response in ("failure", "passthrough"):
                self.configure({"method":"GET", "path":"/hello", "delay_ms":125, "response":response})
                self.gateway.get(self.mock + "/hello")
        self.assertEqual(observed, [0.125, 0.125])

    def test_cross_process_counters_reserve_exactly_n(self):
        self.configure({"method":"GET", "path":"/hello", "response":"failure", "failure_count":7})
        with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as executor:
            actions = list(executor.map(consume_in_process, [str(self.project.directory)] * 24))
        self.assertEqual(actions.count("failure"), 7)
        self.assertEqual(actions.count("passthrough"), 17)
        self.assertEqual(get_scenarios(self.project.id)["rules"][0]["remaining_failures"], 0)

    def test_cancelled_delay_consumes_reservation_and_releases_lease(self):
        self.configure({"method":"GET", "path":"/hello", "response":"failure", "failure_count":1, "delay_ms":10000})
        worker = SimpleNamespace(project=self.project, manifest=self.manifest)
        request = Request({"type":"http", "method":"GET", "path":self.mock + "/hello",
                           "headers":[], "app":gateway})
        manager = gateway.state.worker_manager

        async def scenario():
            delaying = asyncio.Event()
            async def delay(seconds):
                self.assertEqual(seconds, 10)
                delaying.set()
                await asyncio.Event().wait()
            with patch.object(manager, "_get_or_start", return_value=worker), patch("reqlica.runtime.app.asyncio.sleep", side_effect=delay):
                task = asyncio.create_task(proxy(request, self.project.id))
                async with asyncio.timeout(5):
                    await delaying.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(manager._requests, {})

        asyncio.run(scenario())
        self.assertEqual(get_scenarios(self.project.id)["rules"][0]["remaining_failures"], 0)

    def test_conflicts_validation_and_corrupt_configuration(self):
        before = get_scenarios(self.project.id)
        self.configure({"method":"GET", "path":"/hello"})
        reply = self.console.put(self.url, json={"expected_revision":before["revision"], "rules":[]})
        self.assertEqual(reply.status_code, 409)
        for extra in ({"delay_ms":30001}, {"delay_ms":True}, {"status_code":200},
                      {"response":"empty", "failure_count":2}, {"path":"/hello?q=1"},
                      {"body":"x" * 17000}, {"path":"/{id:unknown}"}):
            reply = self.console.put(self.url, json={"expected_revision":get_scenarios(self.project.id)["revision"],
                "rules":[{"method":"GET", "path":"/hello", **extra}]})
            self.assertEqual(reply.status_code, 422, reply.text)
        duplicate = {"method":"GET", "path":"/hello"}
        with self.assertRaises(ValueError):
            ScenarioWrite(expected_revision="0" * 32, rules=[duplicate, duplicate])
        (self.project.directory / SCENARIO_PATH).write_text('{invalid')
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
        self.assertEqual(self.console.get(self.url).status_code, 409)
