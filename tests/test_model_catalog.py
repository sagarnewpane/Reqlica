"""Model discovery uses provider catalogs, not model-name allowlists."""

import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from reqlica.console.app import create_app
from reqlica.generation import catalog
from reqlica.generation.errors import GenerationError
from reqlica.generation.schemas import ProviderEndpoint, AgentRequest


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch.object(catalog, "_catalog", {}))
        self.enterContext(patch.object(catalog, "_cached_at", 0.0))
        self.requests, self.options = [], []
        self.payload = {"data": [{"id": "brand-new-model", "name": "New model"}]}
        self.status = 200
        self.raw = None
        self.exception = None
        client_class = httpx.Client

        def handle(request):
            self.requests.append(request)
            if self.exception:
                raise self.exception("private network detail", request=request)
            return httpx.Response(self.status, content=self.raw) if self.raw is not None else httpx.Response(self.status, json=self.payload)

        def factory(**kwargs):
            self.options.append(kwargs)
            return client_class(**kwargs, transport=httpx.MockTransport(handle))
        self.factory = factory

    def discover(self, provider="compatible", base_url="https://provider.example/v1", key=None):
        with patch("reqlica.generation.catalog.httpx.Client", side_effect=self.factory):
            return catalog.discover_models(ProviderEndpoint(provider=provider, base_url=base_url), SecretStr(key) if key else None)

    def test_groq_fetches_arbitrary_ids_with_browser_or_server_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "server-secret"}):
            self.assertEqual(self.discover("groq", None)["models"][0]["id"], "brand-new-model")
            self.assertEqual(self.requests[-1].headers["authorization"], "Bearer server-secret")
            self.discover("groq", None, "browser-key")
            self.assertEqual(self.requests[-1].headers["authorization"], "Bearer browser-key")
        self.assertEqual(str(self.requests[-1].url), catalog.GROQ_BASE_URL + "/models")
        self.assertFalse(self.options[-1]["trust_env"])
        self.assertFalse(self.options[-1]["follow_redirects"])
        self.assertEqual(self.requests[-1].content, b"")
        self.assertEqual(AgentRequest(message="x", model="brand-new-model").model, "brand-new-model")
        with self.assertRaises(ValidationError):
            AgentRequest(message="x")

    def test_groq_needs_a_key_and_custom_providers_never_inherit_it(self):
        with self.assertRaises(GenerationError):
            self.discover("groq", None)
        self.assertEqual(self.requests, [])
        with patch.dict(os.environ, {"GROQ_API_KEY": "server-secret"}):
            self.discover()
            self.assertNotIn("authorization", self.requests[-1].headers)
            self.discover("compatible", catalog.GROQ_BASE_URL, "explicit-key")
            self.assertEqual(self.requests[-1].headers["authorization"], "Bearer explicit-key")

    def test_openrouter_catalog_is_public_exact_and_refreshable(self):
        model = {"id": "new/model:free", "name": "New model", "supported_parameters": ["tools", "reasoning"], "reasoning": {"mandatory": True, "supported_efforts": ["high", "max"]}, "private": "never return this"}
        self.payload = {"data": [model]}
        first = self.discover("compatible", catalog.OPENROUTER_BASE, "unused-key")
        self.assertNotIn("authorization", self.requests[-1].headers)
        self.assertEqual(first["models"][0]["reasoning"]["supported_efforts"], ["high", "max"])
        self.assertNotIn("private", first["models"][0])
        with patch("reqlica.generation.catalog.httpx.Client", side_effect=self.factory):
            self.assertIsNone(catalog.openrouter_model("new/model"))
            self.assertEqual(catalog.openrouter_model("new/model:free")["id"], "new/model:free")
        self.assertEqual(len(self.requests), 1)
        self.payload = {"data": [{"id": "another-new-id"}]}
        self.discover("compatible", catalog.OPENROUTER_BASE)
        self.assertEqual(len(self.requests), 2)

    def test_endpoint_diagnostics_preserve_the_exact_variant_and_send_no_key(self):
        model = "unlisted/model:free"
        self.payload = {"data": {"id": model, "endpoints": []}}
        with patch("reqlica.generation.catalog.httpx.Client", side_effect=self.factory):
            self.assertEqual(catalog.openrouter_availability(model), {"endpoint_count": 0})
        self.assertEqual(self.requests[-1].url.path, "/api/v1/models/unlisted/model:free/endpoints")
        self.assertNotIn("authorization", self.requests[-1].headers)
        self.assertEqual(self.requests[-1].content, b"")
        self.payload = {"data": {"id": model, "endpoints": [{"provider_name": "example"}]}}
        with patch("reqlica.generation.catalog.httpx.Client", side_effect=self.factory):
            self.assertEqual(catalog.openrouter_availability(model), {"endpoint_count": 1})

    def test_missing_mismatched_and_unreachable_endpoint_metadata_is_unknown(self):
        for payload in ({"data": {"id": "different/model", "endpoints": []}}, {"data": {}}, None):
            self.payload = payload
            with patch("reqlica.generation.catalog.httpx.Client", side_effect=self.factory):
                self.assertIsNone(catalog.openrouter_availability("unlisted/model:free"))
        self.status = 404
        with patch("reqlica.generation.catalog.httpx.Client", side_effect=self.factory):
            self.assertIsNone(catalog.openrouter_availability("unlisted/model:free"))

    def test_ollama_lists_installed_models_without_pulling_or_inference(self):
        self.payload = {"models": [{"name": "local-custom:latest", "model": "local-custom:latest", "details": {"private": "discard"}}]}
        for base in ("http://localhost:11434", "http://localhost:11434/api", "http://localhost:11434/v1"):
            result = self.discover("ollama", base)
            self.assertEqual(result["models"], [{"id": "local-custom:latest", "name": "local-custom:latest"}])
            self.assertEqual(str(self.requests[-1].url), "http://localhost:11434/api/tags")
            self.assertEqual(self.requests[-1].method, "GET")
            self.assertNotIn("authorization", self.requests[-1].headers)

    def test_auth_lists_are_not_shared_between_profiles(self):
        self.discover(key="key-one")
        self.payload = {"data": [{"id": "different-account-model"}]}
        result = self.discover(key="key-two")
        self.assertEqual(result["models"][0]["id"], "different-account-model")
        self.assertEqual(len(self.requests), 2)

    def test_empty_duplicates_invalid_and_inactive_records(self):
        self.payload = {"data": [{"id": "x"}, {"id": "x"}, {"id": "gone", "active": False}, None, {"id": 3}, {"id": " "}]}
        self.assertEqual(self.discover()["models"], [{"id": "x", "name": "x"}])
        self.payload = {"data": []}
        self.assertEqual(self.discover()["models"], [])

    def test_failures_are_bounded_and_never_echo_provider_body_or_keys(self):
        for status in (401, 403, 302, 404, 429, 500):
            self.status = status
            self.payload = {"error": "private-key private server data"}
            with self.subTest(status=status), self.assertRaises(GenerationError) as caught:
                self.discover(key="private-key")
            self.assertNotIn("private", str(caught.exception))
        self.status = 200
        for payload in (None, [], {"data": "bad"}):
            self.payload = payload
            with self.assertRaises(GenerationError):
                self.discover()
        self.raw = b"x" * (8 * 1024 * 1024 + 1)
        with self.assertRaises(GenerationError):
            self.discover()
        self.raw = None
        self.exception = httpx.ReadTimeout
        with self.assertRaises(GenerationError):
            self.discover()

    def test_endpoint_validation_blocks_wrong_origins_and_url_secrets(self):
        for provider, url in (("groq", "https://other.example"), ("openai", "https://other.example"), ("compatible", "http://remote.example"), ("ollama", "https://u:p@example.com"), ("ollama", "https://example.com?key=secret")):
            with self.subTest(provider=provider, url=url), self.assertRaises(ValidationError):
                ProviderEndpoint(provider=provider, base_url=url)

    def test_route_accepts_model_free_settings_and_passes_only_header_key(self):
        with TestClient(create_app()) as client, patch("reqlica.console.routes.generation.discover_models", return_value={"models": [], "message": "No models"}) as fetch:
            response = client.post("/api/ai/models", json={"provider": "groq"}, headers={"X-Reqlica-Provider-Key": "header-only-secret"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(fetch.call_args.args[1].get_secret_value(), "header-only-secret")
            self.assertNotIn("secret", response.text)
            self.assertEqual(client.post("/api/ai/models", json={"provider": "groq", "api_key": "forbidden"}).status_code, 422)
            fetch.side_effect = GenerationError("Unavailable", 503)
            self.assertEqual(client.post("/api/ai/models", json={"provider": "groq"}).headers["cache-control"], "no-store")
