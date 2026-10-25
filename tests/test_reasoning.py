"""Model-aware controls do not turn unsupported/hidden reasoning into an off claim."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation.errors import GenerationError
from reqlica.generation.catalog import GROQ_BASE_URL
from reqlica.generation.reasoning import OPENROUTER_BASE, reasoning_options, reasoning_parameters
from reqlica.generation.schemas import AgentRequest


class ReasoningTests(unittest.TestCase):
    def test_cerebras_qwen_options_and_native_efforts(self):
        base = "https://api.cerebras.ai/v1"
        options = reasoning_options("compatible", "qwen-3.8-27b", base)
        self.assertEqual(options["modes"], ["default", "off", "low", "medium", "high"])
        for mode, effort in [("off", "none"), ("low", "low"), ("medium", "medium"), ("high", "high")]:
            request = AgentRequest(message="x", provider="compatible", base_url=base,
                                   model="qwen-3.8-27b", reasoning_mode=mode)
            self.assertEqual(reasoning_parameters(request), {"reasoning_effort": effort})
        for model, endpoint in [("qwen-unknown", base), ("qwen-3.8-27b", "https://other.example/v1")]:
            self.assertEqual(reasoning_options("compatible", model, endpoint)["modes"], ["default", "off"])
        with TestClient(create_app()) as client:
            response = client.post("/api/ai/reasoning-options", json={"provider": "compatible", "base_url": base, "model": "qwen-3.8-27b"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), options)

    def request(self, **kwargs):
        return AgentRequest(message="x", provider="compatible", base_url=OPENROUTER_BASE, model="test-model", **kwargs)

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

    def test_unknown_and_custom_endpoints_accept_off_without_unverified_parameters(self):
        with patch("reqlica.generation.reasoning._openrouter_model") as lookup:
            self.assertEqual(reasoning_options("compatible", "x", "https://other.example/v1")["modes"], ["default", "off"])
            lookup.assert_not_called()
            self.assertEqual(reasoning_parameters(self.request(reasoning_mode="default")), {})
            lookup.assert_not_called()
        for provider in ("groq", "openai", "ollama", "compatible"):
            request = AgentRequest(message="x", provider=provider, model="unverified-model",
                                   base_url="https://other.example/v1" if provider == "compatible" else None)
            self.assertEqual(request.reasoning_mode, "off")
            self.assertEqual(reasoning_parameters(request), {})
            self.assertIn("may still run", reasoning_options(provider, request.model, request.base_url)["message"])

    def test_non_catalog_models_never_get_inferred_reasoning_controls(self):
        for provider in ("groq", "openai", "compatible"):
            for model in ("brand-new-model", "a-model-with-a-reasoning-sounding-name"):
                request = AgentRequest(message="x", provider=provider, model=model, base_url=GROQ_BASE_URL if provider != "openai" else None)
                options = reasoning_options(provider, model, request.base_url)
                self.assertEqual(options["modes"], ["default", "off"])
                self.assertIn("may still run", options["message"])
                self.assertEqual(reasoning_parameters(request), {})
                with self.assertRaises(GenerationError):
                    reasoning_parameters(request.model_copy(update={"reasoning_mode": "high"}))

    def test_global_options_endpoint_needs_no_project_or_credentials(self):
        with TestClient(create_app()) as client, patch("reqlica.generation.reasoning._openrouter_model", return_value={"mandatory": False}):
            response = client.post("/api/ai/reasoning-options", json={"provider": "compatible", "base_url": OPENROUTER_BASE, "model": "nvidia/nemotron-3.5-lightning:free"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["modes"], ["default", "off"])
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_lookup_failure_does_not_silently_apply_defaults(self):
        with patch("reqlica.generation.reasoning._openrouter_model", side_effect=GenerationError("Lookup unavailable", 503)):
            with self.assertRaises(GenerationError):
                reasoning_parameters(self.request(reasoning_mode="off"))

    def test_default_never_requires_an_explicit_supported_effort(self):
        with patch("reqlica.generation.reasoning.reasoning_options", side_effect=AssertionError("Default must omit the override")):
            self.assertEqual(reasoning_parameters(self.request(reasoning_mode="default")), {})

    def test_unsupported_mode_reports_saved_choice_and_available_options(self):
        with patch("reqlica.generation.reasoning._openrouter_model", return_value=None):
            with self.assertRaises(GenerationError) as caught:
                reasoning_parameters(self.request(reasoning_mode="low"))
        self.assertEqual(caught.exception.detail["code"], "unsupported_reasoning_mode")
        self.assertEqual(caught.exception.detail["reasoning_mode"], "low")
        self.assertEqual(caught.exception.detail["supported_modes"], ["default", "off"])

    def test_omitted_reasoning_disables_supported_models(self):
        request = AgentRequest(message="x", provider="compatible", model="qwen-3.8-27b",
                               base_url="https://api.cerebras.ai/v1")
        self.assertEqual(reasoning_parameters(request), {"reasoning_effort": "none"})
        with patch("reqlica.generation.reasoning._openrouter_model", return_value={"mandatory": False}):
            self.assertEqual(reasoning_parameters(self.request()), {"extra_body": {"reasoning": {"enabled": False}}})
