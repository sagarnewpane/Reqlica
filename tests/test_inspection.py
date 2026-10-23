"""Request inspection is bounded, private, correlated and independent of responses."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from reqlica.projects.inspection import HISTORY_PATH, read_logs, record_event
from reqlica.projects.versions import code_directory
from test_scenarios import ScenarioFixture


class InspectionTests(ScenarioFixture):
    def test_metadata_correlation_and_no_credentials_or_payloads(self):
        reply = self.gateway.get(self.mock + "/hello?password=never-store", headers={"Authorization":"Bearer private-token", "X-Reqlica-Request-ID":"a" * 32})
        self.assertEqual(reply.status_code, 200)
        request_id = reply.headers["x-reqlica-request-id"]
        self.assertNotEqual(request_id, "a" * 32)
        url = f"/api/projects/{self.project.id}/logs"
        page = self.console.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-store")
        event = page.json()["events"][0]
        self.assertEqual(event["request_id"], request_id)
        self.assertEqual(event["status_code"], 200)
        self.assertEqual(event["path"], "/hello")
        self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertGreater(event["response_bytes"], 0)
        self.assertNotIn("never-store", page.text)
        self.assertNotIn("private-token", page.text)
        self.assertEqual(self.console.get(url + f"/{event['id']}").json(), event)
        self.assertEqual(read_logs(self.other.id)["events"], [])
        self.assertEqual(self.console.get(url + "/999999").status_code, 404)
        self.assertEqual(self.console.get(url, params={"limit":0}).status_code, 422)
        self.assertEqual(self.console.get(url, params={"kind":"unknown"}).status_code, 422)
        self.assertEqual(self.console.get(url, params={"request_id":request_id}).json()["events"], [event])

    def test_errors_include_project_frames_without_messages_or_locals(self):
        code = code_directory(self.project)
        (code / "endpoints/hello.py").write_text('def hello():\n    private = "private-value"\n    raise ValueError(private)\n')
        reply = self.gateway.get(self.mock + "/hello")
        self.assertEqual(reply.status_code, 500)
        events = read_logs(self.project.id, request_id=reply.headers["x-reqlica-request-id"])["events"]
        self.assertEqual({e["kind"] for e in events}, {"request", "runtime_error"})
        error = next(e for e in events if e["kind"] == "runtime_error")
        self.assertEqual(error["exception_type"], "ValueError")
        self.assertIn({"file":"endpoints/hello.py", "line":3, "function":"hello"}, error["frames"])
        self.assertNotIn("private-value", json.dumps(events))
        self.assertNotIn("raise ValueError", json.dumps(events))

    def test_gateway_errors_and_startup_failures(self):
        code = code_directory(self.project)
        (code / "endpoints/hello.py").write_text('raise RuntimeError("startup secret")\n')
        reply = self.gateway.get(self.mock + "/hello")
        self.assertEqual(reply.status_code, 502)
        events = read_logs(self.project.id)["events"]
        self.assertEqual({e["kind"] for e in events}, {"startup_error", "request"})
        self.assertNotIn("startup secret", json.dumps(events))
        self.assertEqual(next(e for e in events if e["kind"] == "request")["outcome"], "gateway_error")

    def test_retention_pagination_and_concurrent_writers(self):
        with patch("reqlica.projects.inspection.MAX_EVENTS", 7):
            with ThreadPoolExecutor(4) as executor:
                list(executor.map(lambda i: record_event(self.project.directory, {"kind":"request", "request_id":str(i)}), range(20)))
        first = read_logs(self.project.id, limit=3)
        second = read_logs(self.project.id, limit=3, before_id=first["next_before_id"])
        third = read_logs(self.project.id, limit=3, before_id=second["next_before_id"])
        ids = [e["id"] for page in (first, second, third) for e in page["events"]]
        self.assertEqual(len(ids), 7)
        self.assertEqual(len(set(ids)), 7)
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertIsNone(third["next_before_id"])
        self.assertEqual(read_logs(self.project.id, kind="runtime_error")["events"], [])

    def test_corrupt_history_cannot_break_requests(self):
        self.gateway.get(self.mock + "/hello")
        path = self.project.directory / HISTORY_PATH
        path.write_bytes(b"not a database")
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 200)
        self.assertEqual(self.console.get(f"/api/projects/{self.project.id}/logs").status_code, 409)

    def test_synthetic_and_auth_failures_are_logged(self):
        self.configure({"method":"GET", "path":"/hello", "response":"empty", "rate_limit":{"requests":1, "window_seconds":60}})
        self.assertEqual(self.gateway.get(self.mock + "/hello").json(), [])
        self.assertEqual(self.gateway.get(self.mock + "/hello").status_code, 429)
        statuses = [e["status_code"] for e in read_logs(self.project.id)["events"] if e["kind"] == "request"]
        self.assertEqual(statuses, [429, 200])
