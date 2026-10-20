"""Activity is observable during a request without exposing request/model data."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI
from pydantic import SecretStr
from reqlica.console.app import create_app
from reqlica.generation import progress, provider
from reqlica.generation.errors import GenerationError
from reqlica.generation.schemas import GenerationRequest


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(TestClient(create_app()))
        self.root = "/api/projects/project"
        self.activity_id = "a" * 32

    def tearDown(self):
        with progress._lock:
            progress._activities.clear()

    def test_live_stages_and_completion_are_scoped_and_content_free(self):
        reached, release = Event(), Event()

        def review(*args):
            progress.report("provider_request")
            reached.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release request")
            progress.report("provider_response")
            progress.report("checking")
            return {"status": "ready"}

        with patch("reqlica.console.routes.generation.review_requirements", review):
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(
                    self.client.post, self.root + "/clarify",
                    headers={"X-Reqlica-Activity-ID": self.activity_id, "X-Reqlica-Groq-Key": "private-key"},
                    json={"requirements": "private-prompt"},
                )
                try:
                    self.assertTrue(reached.wait(5))
                    response = self.client.get(self.root + "/activity/" + self.activity_id)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertEqual([e["stage"] for e in response.json()["events"]], ["received", "provider_request"])
                    self.assertNotIn("private", response.text)
                    self.assertEqual(self.client.get("/api/projects/other/activity/" + self.activity_id).status_code, 404)
                finally:
                    release.set()
                self.assertEqual(pending.result().status_code, 200)
        events = self.client.get(self.root + "/activity/" + self.activity_id).json()["events"]
        self.assertEqual(events[-1]["stage"], "completed")
        self.assertTrue(all(isinstance(e["elapsed_ms"], int) for e in events))
        self.assertIsNone(progress._current.get())

    def test_failure_before_provider_contact_is_distinct(self):
        with patch("reqlica.console.routes.generation.review_requirements", side_effect=GenerationError("Missing key", 503)):
            response = self.client.post(self.root + "/clarify", json={"requirements": "x"}, headers={"X-Reqlica-Activity-ID": self.activity_id})
        self.assertEqual(response.status_code, 503)
        events = self.client.get(self.root + "/activity/" + self.activity_id).json()["events"]
        self.assertEqual([e["stage"] for e in events], ["received", "failed"])

    def test_activity_is_optional_and_invalid_ids_are_rejected(self):
        with patch("reqlica.console.routes.generation.review_requirements", return_value={"status": "ready"}):
            self.assertEqual(self.client.post(self.root + "/clarify", json={"requirements": "x"}).status_code, 200)
            self.assertEqual(self.client.post(self.root + "/clarify", json={"requirements": "x"}, headers={"X-Reqlica-Activity-ID": "bad"}).status_code, 422)
        self.assertEqual(len(progress._activities), 0)

    def test_expiration_capacity_and_event_bounds(self):
        with patch.object(progress, "MAX_ACTIVITIES", 2):
            for identifier in ["first", "second", "third"]:
                with progress.track("project", identifier):
                    for _ in range(100):
                        progress.report("checking")
            self.assertEqual(len(progress._activities), 2)
            with self.assertRaises(FileNotFoundError):
                progress.inspect("project", "first")
            self.assertEqual(len(progress.inspect("project", "third")["events"]), progress.MAX_EVENTS)
        with patch.object(progress, "TTL_SECONDS", -1):
            with self.assertRaises(FileNotFoundError):
                progress.inspect("project", "third")

    def test_provider_http_hooks_report_success_and_rejection(self):
        for status in [200, 429]:
            payload = {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}]} if status == 200 else {"error": {"message": "private-provider-detail"}}

            def factory(**kwargs):
                kwargs["http_client"] = httpx.Client(
                    transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
                    event_hooks=kwargs["http_client"].event_hooks,
                )
                return OpenAI(**kwargs)

            with patch("reqlica.generation.provider.OpenAI", side_effect=factory):
                try:
                    with progress.track("project", str(status)):
                        provider.complete([{"role": "user", "content": "private-prompt"}], GenerationRequest(requirements="x"), SecretStr("private-key"))
                except GenerationError:
                    self.assertEqual(status, 429)
            events = progress.inspect("project", str(status))["events"]
            stages = [e["stage"] for e in events]
            self.assertEqual(stages[:3], ["received", "format_json_schema", "provider_request"])
            self.assertIn("provider_accepted" if status == 200 else "provider_rejected", stages)
            self.assertEqual(stages[-1], "completed" if status == 200 else "failed")
            self.assertNotIn("private", str(events))
