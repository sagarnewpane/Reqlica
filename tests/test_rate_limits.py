"""Rate limits isolate routes/users and persist exact cross-process quotas."""

import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

from reqlica.projects.scenarios import consume_scenario, get_scenarios
from reqlica.projects.store import Manifest
from test_scenarios import ScenarioFixture


def consume_rate_in_process(directory):
    manifest = Manifest(endpoints=[{"method":"GET", "path":"/hello", "handler":"endpoints/hello.py:hello"}])
    action = consume_scenario(Path(directory), "GET", "/hello", manifest)
    return action.get("rate_limit_status", 200)


class RateLimitTests(ScenarioFixture):
    def configure_limit(self, scope="route", **extra):
        return self.configure({"method":"GET", "path":"/hello",
            "rate_limit":{"requests":2, "window_seconds":60, "scope":scope}, **extra})

    def test_limit_retry_after_and_window_boundary(self):
        state = self.configure_limit()
        with patch("reqlica.projects.scenarios.time.time", return_value=1000):
            self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
            self.assertEqual(self.gateway.get(self.mock + "/hello?query=yes").status_code, 200)
            reply = self.gateway.get(self.mock + "/hello")
            self.assertEqual(reply.status_code, 429)
            self.assertEqual(reply.json(), {"detail":"rate_limit_exceeded"})
            self.assertEqual(reply.headers["retry-after"], "60")
            self.assertEqual(reply.headers["cache-control"], "no-store")
        with patch("reqlica.projects.scenarios.time.time", return_value=1059.9):
            self.assertEqual(self.gateway.get(self.mock + "/hello").headers["retry-after"], "1")
        with patch("reqlica.projects.scenarios.time.time", return_value=1060):
            self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        self.assertEqual(get_scenarios(self.project.id)["revision"], state["revision"])

    def test_test_user_isolation_and_validation(self):
        self.configure_limit("test_user")
        for headers in ({}, {"X-Reqlica-Test-User":"space invalid"}, [("X-Reqlica-Test-User","one"),("X-Reqlica-Test-User","two")]):
            self.assertEqual(self.gateway.get(self.mock + "/hello", headers=headers).status_code, 400)
        for user in ("alice", "bob"):
            headers = {"X-Reqlica-Test-User":user}
            for _ in range(2):
                self.assertEqual(self.gateway.get(self.mock + "/hello", headers=headers).status_code, 200)
            self.assertEqual(self.gateway.get(self.mock + "/hello", headers=headers).status_code, 429)
        stored = (self.project.directory / ".reqlica/scenarios.json").read_text()
        self.assertNotIn("alice", stored)
        self.assertNotIn("bob", stored)

    def test_auth_first_then_rate_then_failure_count(self):
        self.configure_limit(response="failure", failure_count=3)
        auth_url = f"/api/projects/{self.project.id}/mock-auth"
        revision = self.console.get(auth_url).json()["revision"]
        self.console.put(auth_url, json={"expected_revision":revision, "mode":"bearer", "credentials":[{"name":"test", "value":"token"}]})
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 401)
        for _ in range(2):
            self.assertEqual(self.gateway.get(self.mock + "/hello", headers={"Authorization":"Bearer token"}).status_code, 503)
        self.assertEqual(self.gateway.get(self.mock + "/hello", headers={"Authorization":"Bearer token"}).status_code, 429)
        self.assertEqual(get_scenarios(self.project.id)["rules"][0]["remaining_failures"], 1)

    def test_cross_process_exact_quota(self):
        self.configure({"method":"GET", "path":"/hello", "rate_limit":{"requests":7, "window_seconds":3600}})
        with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as executor:
            results = list(executor.map(consume_rate_in_process, [str(self.project.directory)] * 24))
        self.assertEqual(results.count(200), 7)
        self.assertEqual(results.count(429), 17)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 429)

    def test_capacity_expires_and_replacement_resets(self):
        self.configure_limit("test_user")
        with patch("reqlica.projects.scenarios.MAX_RATE_BUCKETS", 1), patch("reqlica.projects.scenarios.time.time", return_value=1000):
            self.assertEqual(self.gateway.get(self.mock + "/hello", headers={"X-Reqlica-Test-User":"first"}).status_code, 200)
            reply = self.gateway.get(self.mock + "/hello", headers={"X-Reqlica-Test-User":"second"})
            self.assertEqual(reply.status_code, 429)
            self.assertEqual(reply.json()["detail"], "rate_limit_capacity")
        with patch("reqlica.projects.scenarios.time.time", return_value=1060):
            self.assertEqual(self.gateway.get(self.mock + "/hello", headers={"X-Reqlica-Test-User":"second"}).status_code, 200)
        self.configure_limit()
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        self.configure()
        for _ in range(3):
            self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)

    def test_route_project_isolation_head_and_validation(self):
        self.configure({"method":"GET", "path":"/items/{item_id}", "rate_limit":{"requests":1,"window_seconds":60}},
                       {"method":"HEAD", "path":"/hello", "rate_limit":{"requests":1,"window_seconds":60}})
        self.assertEqual(self.gateway.get(self.mock + "/items/one").status_code, 200)
        self.assertEqual(self.gateway.get(self.mock + "/items/two").status_code, 429)
        self.assertEqual(self.gateway.get(self.mock + "/items/static").status_code, 200)
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        self.assertEqual(self.gateway.get(f"/mock/{self.other.id}/hello").status_code, 200)
        self.assertEqual(self.gateway.head(self.mock + "/hello").status_code, 200)
        reply = self.gateway.head(self.mock + "/hello")
        self.assertEqual(reply.status_code, 429)
        self.assertEqual(reply.content, b"")
        for limit in ({"requests":0,"window_seconds":60}, {"requests":True,"window_seconds":60},
                      {"requests":1,"window_seconds":86401}, {"requests":1,"window_seconds":1,"scope":"ip"}):
            state = get_scenarios(self.project.id)
            reply = self.console.put(self.url, json={"expected_revision":state["revision"], "rules":[{"method":"GET", "path":"/hello", "rate_limit":limit}]})
            self.assertEqual(reply.status_code, 422)

    def test_corrupt_rate_counters_fail_closed(self):
        self.configure_limit()
        self.gateway.get(self.mock + "/hello")
        path = self.project.directory / ".reqlica/scenarios.json"
        stored = json.loads(path.read_text())
        stored["rate_buckets"]["0:route"]["count"] = 999
        path.write_text(json.dumps(stored))
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 503)
