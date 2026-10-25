"""Timeout defaults and boundary alignment without real waits or provider calls."""

import re
import unittest
from pathlib import Path

from pydantic import ValidationError
from reqlica.generation.schemas import AgentRequest


class TimeoutTests(unittest.TestCase):
    def test_api_defaults_and_bounds(self):
        for cls, body in [(AgentRequest, {"message": "x", "model": "test-tool-model"})]:
            self.assertEqual(cls(**body).timeout_seconds, 300)
            for seconds in [30, 100, 600]:
                self.assertEqual(cls(**body, timeout_seconds=seconds).timeout_seconds, seconds)
            for seconds in [29, 601, "300", 30.5, True]:
                with self.assertRaises(ValidationError):
                    cls(**body, timeout_seconds=seconds)

    def test_proxy_alignment_is_bounded_and_only_applies_to_ai(self):
        html = (Path(__file__).parents[1] / "backend/reqlica/reqlicav1.html").read_text()
        source = html.split("def upstream_timeout(", 1)[1].split("class Handler(", 1)[0]
        namespace = {"re": re}
        exec("def upstream_timeout(" + source, namespace)
        timeout = namespace["upstream_timeout"]
        for route in ["agent-runs/j/step"]:
            path = "/api/projects/p/" + route
            self.assertEqual(timeout("console", path, "POST"), 330)
            self.assertEqual(timeout("console", path, "POST", "100"), 130)
            self.assertEqual(timeout("console", path, "POST", "600"), 630)
            for value in ["29", "601", "bad"]:
                with self.assertRaises(ValueError):
                    timeout("console", path, "POST", value)
            self.assertEqual(timeout("gateway", path, "POST", "600"), 80)
            self.assertEqual(timeout("console", path, "GET", "600"), 80)
        self.assertEqual(timeout("console", "/api/projects/p/data/insert", "POST", "600"), 80)
        # Catch syntax errors in the embedded server without starting it.
        compile(html.split("<!-- REQLICA_LOCAL_SERVER\n", 1)[1].split("\nREQLICA_LOCAL_SERVER -->", 1)[0], "embedded bridge", "exec")
