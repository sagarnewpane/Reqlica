"""Model-aware controls do not turn unsupported/hidden reasoning into an off claim."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation.errors import GenerationError
from reqlica.generation.reasoning import OPENROUTER_BASE, reasoning_options, reasoning_parameters
from reqlica.generation.schemas import GenerationRequest


class ReasoningTests(unittest.TestCase):
    def request(self, **kwargs):
        return GenerationRequest(requirements="x", provider="compatible", base_url=OPENROUTER_BASE, model="test-model", **kwargs)

    def test_optional_reasoning_off_and_mandatory_efforts(self):
        with patch("reqlica.generation.reasoning._openrouter_model", return_value={"mandatory": False}):
            self.assertEqual(reasoning_options("compatible", "test-model", OPENROUTER_BASE)["modes"], ["default", "off"])
            self.assertEqual(reasoning_parameters(self.request(reasoning_mode="off")), {"extra_body": {"reasoning": {"enabled": False}}})
        with patch("reqlica.generation.reasoning._openrouter_model", return_value={"mandatory": True, "supported_efforts": ["max", "low"]}):
            self.assertEqual(reasoning_options("compatible", "test-model", OPENROUTER_BASE)["modes"], ["default", "low", "max"])
            with self.assertRaises(GenerationError) as error:
                reasoning_parameters(self.request(reasoning_mode="off"))
            self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(reasoning_parameters(self.request(reasoning_mode="low")), {"extra_body": {"reasoning": {"effort": "low"}}})

    def test_unknown_and_custom_endpoints_only_offer_defaults(self):
        with patch("reqlica.generation.reasoning._openrouter_model") as lookup:
            self.assertEqual(reasoning_options("compatible", "x", "https://other.example/v1")["modes"], ["default"])
            lookup.assert_not_called()
            self.assertEqual(reasoning_parameters(self.request()), {})
            lookup.assert_not_called()
        self.assertNotIn("off", reasoning_options("openai", "unverified-model")["modes"])
        self.assertNotIn("off", reasoning_options("groq", "openai/gpt-oss-120b")["modes"])

    def test_openai_documented_efforts(self):
        self.assertIn("off", reasoning_options("openai", "gpt-5.1")["modes"])
        self.assertIn("xhigh", reasoning_options("openai", "gpt-5.2-2025-12-11")["modes"])
        self.assertNotIn("off", reasoning_options("openai", "gpt-5-mini")["modes"])
        request = GenerationRequest(requirements="x", provider="openai", model="gpt-5.2", reasoning_mode="off")
        self.assertEqual(reasoning_parameters(request), {"reasoning_effort": "none"})

    def test_global_options_endpoint_needs_no_project_or_credentials(self):
        with TestClient(create_app()) as client, patch("reqlica.generation.reasoning._openrouter_model", return_value={"mandatory": False}), patch("reqlica.console.routes.generation.output_mode", return_value="text"):
            response = client.post("/api/ai/reasoning-options", json={"provider": "compatible", "base_url": OPENROUTER_BASE, "model": "nvidia/nemotron-3.5-lightning:free"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["modes"], ["default", "off"])
            self.assertEqual(response.json()["output_mode"], "text")
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_lookup_failure_does_not_silently_apply_defaults(self):
        with patch("reqlica.generation.reasoning._openrouter_model", side_effect=GenerationError("Lookup unavailable", 503)):
            with self.assertRaises(GenerationError):
                reasoning_parameters(self.request(reasoning_mode="off"))
