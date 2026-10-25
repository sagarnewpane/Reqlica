"""Provider transport, auth, timeouts, and sanitized failures for Project AI."""
import json
import os
import unittest
from unittest.mock import patch
import httpx
from pydantic import SecretStr
from reqlica.generation import provider
from reqlica.generation.errors import GenerationError
from reqlica.generation.schemas import AgentRequest
from ai_fixtures import ProviderFixture

class ProviderTests(ProviderFixture, unittest.TestCase):
    def test_cerebras_qwen_reasoning_reaches_native_wire_field(self):
        for mode, effort in [("off", "none"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("default", None)]:
            request = AgentRequest(message="Explain", provider="compatible", model="qwen-3.8-27b",
                                   base_url="https://api.cerebras.ai/v1", reasoning_mode=mode)
            provider.complete(self.messages, request, SecretStr("explicit-key"), tools=[])
            body = json.loads(self.requests[-1].content)
            self.assertEqual(body.get("reasoning_effort"), effort)
            self.assertNotIn("reasoning", body)
            self.assertNotIn("reasoning_mode", body)

    def test_64k_output_budget_reaches_each_provider_and_larger_values_are_rejected(self):
        from pydantic import ValidationError
        for kind in ("groq", "openai", "compatible", "ollama"):
            request = AgentRequest(message="Explain", provider=kind, model="test-model", max_tokens=65536,
                                   base_url="http://localhost:11434/v1" if kind in {"compatible", "ollama"} else None)
            provider.complete(self.messages, request, SecretStr("explicit-key"), tools=[])
            body = json.loads(self.requests[-1].content)
            self.assertEqual(body["max_completion_tokens" if kind in {"groq", "openai"} else "max_tokens"], 65536)
            with self.assertRaises(ValidationError):
                AgentRequest(**{**request.model_dump(), "max_tokens": 65537})

    def test_local_ollama_uses_chat_endpoint_without_requiring_or_leaking_a_key(self):
        tools = [{"type": "function", "function": {"name": "read_context", "parameters": {"type": "object", "properties": {}}}}]
        for base in (None, "http://localhost:11434", "http://localhost:11434/api/", "http://localhost:11434/v1/chat/completions/"):
            request = AgentRequest(message="Explain", provider="ollama", model="installed:model", base_url=base)
            with patch.dict(os.environ, {"GROQ_API_KEY": "server-secret", "GROQ_API": "other-secret"}):
                provider.complete(self.messages, request, tools=tools)
            sent = self.requests[-1]
            self.assertEqual(str(sent.url), "http://localhost:11434/v1/chat/completions")
            self.assertEqual(sent.headers["authorization"], "Bearer ollama")
            body = json.loads(sent.content)
            self.assertEqual(body["model"], "installed:model")
            self.assertEqual(body["messages"], self.messages)
            self.assertEqual(body["tools"], tools)
            self.assertEqual(body["max_tokens"], request.max_tokens)
            for name in ("parallel_tool_calls", "tool_choice", "reasoning", "seed"):
                self.assertNotIn(name, body)

    def test_remote_ollama_requires_explicit_key_and_preserves_custom_prefix(self):
        request = AgentRequest(message="Explain", provider="ollama", model="installed:model", base_url="https://local.example/ollama/api")
        with patch.dict(os.environ, {"GROQ_API_KEY": "server-secret"}):
            with self.assertRaises(GenerationError):
                provider.complete(self.messages, request, tools=[])
            self.assertEqual(self.requests, [])
            provider.complete(self.messages, request, SecretStr("explicit-key"), tools=[])
        self.assertEqual(str(self.requests[-1].url), "https://local.example/ollama/v1/chat/completions")
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer explicit-key")

    def test_compatible_uses_configured_origin_model_and_explicit_key(self):
        request = AgentRequest(
            message="Catalog", provider="compatible", model="custom-model",
            base_url="https://provider.example/api/v1/chat/completions/",
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": "server-groq-secret"}):
            provider.complete(self.messages, request, SecretStr("custom-provider-key"), tools=[])
        sent = self.requests[0]
        self.assertEqual(str(sent.url), "https://provider.example/api/v1/chat/completions")
        self.assertEqual(sent.headers["authorization"], "Bearer custom-provider-key")
        body = json.loads(sent.content)
        self.assertEqual(body["model"], "custom-model")
        self.assertEqual(body["max_tokens"], request.max_tokens)
        self.assertNotIn("response_format", body)
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("seed", body)
        self.assertEqual(body["messages"], self.messages)
        self.assertNotIn("server-groq-secret", sent.content.decode())
        self.assertEqual(len(self.requests), 1)

    def test_custom_provider_never_falls_back_to_server_groq_key(self):
        request = AgentRequest(
            message="Catalog", provider="compatible", model="custom-model",
            base_url="https://provider.example/v1",
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": "server-secret"}):
            with self.assertRaises(GenerationError) as raised:
                provider.complete(self.messages, request, tools=[])
        self.assertIn("selected provider", str(raised.exception))
        self.assertEqual(self.requests, [])

    def test_compatible_errors_name_selected_provider(self):
        self.status = 401
        request = AgentRequest(
            message="Catalog", provider="compatible", model="custom-model",
            base_url="https://provider.example/v1",
        )
        with self.assertRaises(GenerationError) as raised:
            provider.complete(self.messages, request, SecretStr("custom-key"), tools=[])
        self.assertIn("OpenAI-compatible provider", str(raised.exception))
        self.assertNotIn("Groq", str(raised.exception))

    def test_provider_urls_and_models_are_validated(self):
        from pydantic import ValidationError
        for url in (
            "http://provider.example/v1", "file:///tmp/model", "https://name:pass@example.com/v1",
            "https://example.com/v1?key=private", "https://example.com/v1#private",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                AgentRequest(message="Test", provider="compatible", model="custom", base_url=url)
        with self.assertRaises(ValidationError):
            AgentRequest(message="Test", base_url="https://provider.example/v1")
        local = AgentRequest(message="Test", provider="compatible", model="local", base_url="http://localhost:1234/v1/chat/completions")
        self.assertEqual(local.base_url, "http://localhost:1234/v1")

    def test_explicit_temperature_is_preserved_with_fixed_seed(self):
        self.request = self.request.model_copy(update={"temperature": 0.7})
        self.complete()
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["seed"], 42)

    def test_missing_credentials_fail_before_network(self):
        with self.assertRaises(GenerationError) as raised:
            provider.complete(self.messages, self.request, tools=[])
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.requests, [])

    def test_existing_groq_environment_name_is_supported(self):
        with patch.dict(os.environ, {"GROQ_API": "legacy-env-key"}):
            provider.complete(self.messages, self.request, tools=[])
        self.assertEqual(
            self.requests[0].headers["authorization"], "Bearer legacy-env-key"
        )

    def test_provider_errors_are_redacted_and_never_retried(self):
        for status, expected in (
            (401, 502),
            (403, 502),
            (400, 502),
            (413, 413),
            (429, 503),
            (500, 502),
        ):
            with self.subTest(status=status):
                self.requests.clear()
                self.status = status
                self.payload = {
                    "error": {"message": "private-provider-key and raw provider body"}
                }
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertEqual(raised.exception.status_code, expected)
                self.assertNotIn("private-provider-key", str(raised.exception))
                self.assertEqual(len(self.requests), 1)

    def test_timeout_and_connection_errors(self):
        for kind, expected in ((httpx.ReadTimeout, 504), (httpx.ConnectError, 502)):
            with self.subTest(kind=kind):
                self.requests.clear()
                self.exception = kind
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertEqual(raised.exception.status_code, expected)
                self.assertEqual(len(self.requests), 1)

    def test_configurable_response_timeout_is_local_transport_configuration(self):
        for seconds in [30, 100, 300, 600]:
            request = self.request.model_copy(update={"timeout_seconds": seconds})
            provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[])
            self.assertEqual(self.options["timeout"].read, seconds)
            self.assertEqual(self.options["timeout"].connect, 10)
            self.assertEqual(self.requests[-1].extensions["timeout"]["read"], seconds)
            self.assertEqual(self.options["max_retries"], 0)
            self.assertNotIn("timeout_seconds", json.loads(self.requests[-1].content))

    def test_reasoning_choices_reach_correct_provider_wire_fields(self):
        cases = [("compatible", "nvidia/test", "off", {"enabled": False}), ("compatible", "minimax/test", "low", {"effort": "low"})]
        for kind, model, mode, expected in cases:
            request = AgentRequest(message="x", provider=kind, model=model, base_url="https://openrouter.ai/api/v1" if kind == "compatible" else None, reasoning_mode=mode)
            with patch("reqlica.generation.reasoning._openrouter_model", return_value={"mandatory": False, "supported_efforts": ["low"]}):
                provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[])
            body = json.loads(self.requests[-1].content)
            self.assertNotIn("reasoning_mode", body)
            if kind == "compatible":
                self.assertEqual(body["reasoning"], expected)
                self.assertNotIn("reasoning_effort", body)
            else:
                self.assertEqual(body["reasoning_effort"], expected)
                self.assertEqual(body["max_completion_tokens"], request.max_tokens)
                if mode == "low":
                    self.assertNotIn("temperature", body)

    def test_oversized_request_explains_provider_limits_without_echoing_body(self):
        self.status = 413
        self.payload = {"error": {"message": "private-provider-key private source"}}
        with self.assertRaises(GenerationError) as raised:
            self.complete()
        self.assertEqual(raised.exception.status_code, 413)
        self.assertIn("Reduce input/history", str(raised.exception))
        self.assertIn("model and account limits", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(len(self.requests), 1)

    def test_413_preserves_reported_quota_without_echoing_keys_or_organization(self):
        self.status = 413
        self.payload = {"error": {"message": "Request too large for model in organization private-org on tokens per minute (TPM): Limit 8,000, Requested 9,250. private-provider-key private-source"}}
        with self.assertRaises(GenerationError) as caught:
            self.complete()
        detail = caught.exception.detail
        self.assertEqual(detail["diagnostics"]["provider_quota"], {"metric": "tokens_per_minute", "limit": 8000, "requested": 9250})
        self.assertIn("8,000", detail["message"])
        self.assertIn("9,250", detail["message"])
        self.assertIn("waiting alone will not make it fit", detail["message"])
        self.assertIn("retains its saved output budget", detail["message"])
        self.assertNotIn("private", json.dumps(detail))
        self.assertIn("tools_bytes", detail["diagnostics"])
        self.assertEqual(len(self.requests), 1)

    def test_413_only_reports_explicit_metrics_and_bounded_numeric_counts(self):
        self.status = 413
        for text, expected in (
            ("input tokens per minute (ITPM): Limit: 9000 Requested: 9500", {"metric": "input_tokens_per_minute", "limit": 9000, "requested": 9500}),
            ("output tokens per minute (OTPM): Limit=1000 Requested=2000", {"metric": "output_tokens_per_minute", "limit": 1000, "requested": 2000}),
            ("tokens per day (TPD): Limit 200000 Requested 200100", {"metric": "tokens_per_day", "limit": 200000, "requested": 200100}),
            ("tokens per minute: Limit private-key Requested private-source", {"metric": "tokens_per_minute"}),
            ("TPM: Limit 12345678901234567890 Requested 123private", {"metric": "tokens_per_minute"}),
            ("TPM: Limit 8,00 Requested 9.5", {"metric": "tokens_per_minute"}),
        ):
            self.payload = {"error": {"message": text}}
            with self.subTest(text=text), self.assertRaises(GenerationError) as caught:
                self.complete()
            self.assertEqual(caught.exception.detail["diagnostics"]["provider_quota"], expected)
            self.assertNotIn("private", json.dumps(caught.exception.detail))
        for payload in (None, [], {"error": "private-source"}, {"error": {"message": []}}, {"error": {"message": "Limit 8000 Requested 9000 private-source"}}):
            self.payload = payload
            with self.assertRaises(GenerationError) as caught:
                self.complete()
            self.assertNotIn("provider_quota", caught.exception.detail["diagnostics"])
            self.assertNotIn("private", json.dumps(caught.exception.detail))

    def test_default_reasoning_is_left_to_every_provider(self):
        for kind in ("groq", "compatible", "openai"):
            request = AgentRequest(message="x", provider=kind, model="brand-new-model", base_url=provider.GROQ_BASE_URL if kind != "openai" else None, reasoning_mode="default")
            provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[])
            body = json.loads(self.requests[-1].content)
            self.assertEqual(body["model"], "brand-new-model")
            self.assertNotIn("reasoning", body)
            self.assertNotIn("reasoning_effort", body)
            if kind == "openai":
                self.assertIn("max_completion_tokens", body)

    def test_unlisted_model_and_catalog_outage_do_not_block_or_substitute_inference(self):
        request = AgentRequest(message="x", provider="compatible", base_url="https://openrouter.ai/api/v1", model="unlisted/model:free", reasoning_mode="default")
        for options in ({"return_value": None}, {"side_effect": GenerationError("Catalog unavailable", 503)}):
            with self.subTest(options=options), patch("reqlica.generation.provider.openrouter_model", **options):
                provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[])
            body = json.loads(self.requests[-1].content)
            self.assertEqual(body["model"], "unlisted/model:free")
            self.assertNotIn("provider", body)
            self.assertNotIn("parallel_tool_calls", body)
            self.assertNotIn("tool_choice", body)
        self.assertEqual(len(self.requests), 2)

    def test_explicit_openrouter_reasoning_still_requires_parameter_support(self):
        request = AgentRequest(message="x", provider="compatible", base_url="https://openrouter.ai/api/v1", model="optional/model", reasoning_mode="off")
        with patch("reqlica.generation.reasoning._openrouter_model", return_value={"mandatory": False}):
            provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[])
        body = json.loads(self.requests[-1].content)
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["provider"], {"require_parameters": True})

    def test_openrouter_404_reports_empty_endpoint_list_without_retrying_inference(self):
        self.status = 404
        self.payload = {"error": {"message": "Unknown model private-provider-key private-source"}}
        request = AgentRequest(message="x", provider="compatible", base_url="https://openrouter.ai/api/v1", model="unlisted/model:free", reasoning_mode="default")
        with patch("reqlica.generation.provider.openrouter_model", return_value=None), patch("reqlica.generation.provider.openrouter_availability", return_value={"endpoint_count": 0}) as lookup:
            with self.assertRaises(GenerationError) as caught:
                provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[])
        lookup.assert_called_once_with("unlisted/model:free")
        self.assertIn("no provider endpoints for this exact model ID", str(caught.exception))
        self.assertNotIn("private", json.dumps(caught.exception.detail))
        self.assertEqual(len(self.requests), 1)

    def test_openrouter_404_with_unknown_availability_does_not_claim_model_is_invalid(self):
        self.status = 404
        self.payload = {"error": {"message": "private error"}}
        request = AgentRequest(message="x", provider="compatible", base_url="https://openrouter.ai/api/v1", model="unlisted/model:free", reasoning_mode="default")
        with patch("reqlica.generation.provider.openrouter_model", return_value=None), patch("reqlica.generation.provider.openrouter_availability", return_value=None):
            with self.assertRaises(GenerationError) as caught:
                provider.complete(self.messages, request, SecretStr("private-provider-key"), tools=[])
        self.assertIn("public model list alone does not establish availability", str(caught.exception))
        self.assertNotIn("not listed", str(caught.exception))
        self.assertNotIn("private", json.dumps(caught.exception.detail))
        self.assertEqual(len(self.requests), 1)

    def test_bad_requests_have_actionable_reasons_without_raw_provider_content(self):
        cases = [
            ({"code": "json_validate_failed"}, "required JSON schema"),
            ({"code": "json_generation_failed"}, "required JSON schema"),
            (
                {"message": "Generated JSON does not match the expected schema"},
                "required JSON schema",
            ),
            ({"message": "Failed to generate JSON"}, "required JSON schema"),
            ({"code": "context_length_exceeded"}, "context limit"),
            ({"message": "Maximum context window exceeded"}, "context limit"),
            ({"message": "Invalid JSON schema"}, "provider/schema compatibility"),
            ({"param": "max_completion_tokens"}, "Lower Max output tokens"),
            (
                {"message": "max_tokens must be below the model limit"},
                "Lower Max output tokens",
            ),
            ({"code": "model_not_found"}, "selected model"),
            ({"param": "reasoning_effort"}, "generation setting"),
            ({"param": "response_format"}, "generation setting"),
            ({"code": "unrecognized"}, "not return a recognized failure reason"),
        ]
        self.status = 400
        for detail, expected in cases:
            with self.subTest(detail=detail):
                self.requests.clear()
                detail = {
                    **detail,
                    "message": detail.get("message", "")
                    + " private-provider-key private-project-source",
                    "failed_generation": "private generated endpoint source",
                }
                self.payload = {"error": detail}
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertEqual(raised.exception.status_code, 502)
                self.assertIn(expected, str(raised.exception))
                self.assertIn("earlier draft changes are retained", str(raised.exception))
                self.assertNotIn("private", json.dumps(raised.exception.detail))
                self.assertEqual(len(self.requests), 1)

    def test_malformed_bad_request_diagnostics_do_not_break_error_handling(self):
        self.status = 400
        for payload in (
            None,
            "private raw response",
            [],
            {"error": "private raw response"},
            {"error": {"message": [], "code": {}, "param": {}}},
        ):
            with self.subTest(payload=payload):
                self.requests.clear()
                self.payload = payload
                with self.assertRaises(GenerationError) as raised:
                    self.complete()
                self.assertIn("HTTP 400", str(raised.exception))
                self.assertNotIn("private", str(raised.exception))
                self.assertEqual(len(self.requests), 1)

    def test_output_limit_is_distinct_from_schema_failure(self):
        self.payload["choices"][0]["finish_reason"] = "length"
        with self.assertRaises(GenerationError) as raised:
            self.complete()
        self.assertEqual(raised.exception.detail["code"], "output_token_limit")
        self.assertIn("configured output cap is 8,192", str(raised.exception))
        self.assertIn("10 input tokens and 20 generated tokens", str(raised.exception))
        self.assertIn("does not prove", str(raised.exception))
        self.assertEqual(raised.exception.detail["diagnostics"]["usage"], {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        self.assertNotIn("JSON schema", str(raised.exception))

    def test_length_failure_preserves_reasoning_counters_but_not_raw_output(self):
        self.payload["choices"][0].update(finish_reason="length", message={"role": "assistant", "content": "private partial source", "reasoning": "private thinking"})
        self.payload["usage"].update(completion_tokens_details={"reasoning_tokens": 12, "private": "private-key"}, private="private-key")
        with self.assertRaises(GenerationError) as raised:
            self.complete()
        self.assertEqual(raised.exception.detail["diagnostics"]["usage"]["completion_tokens_details"], {"reasoning_tokens": 12})
        self.assertNotIn("private", json.dumps(raised.exception.detail))
        self.payload.pop("usage")
        with self.assertRaises(GenerationError) as raised:
            self.complete()
        self.assertIsNone(raised.exception.detail["diagnostics"]["usage"])
        self.assertIn("did not report token usage", str(raised.exception))

    def test_truncated_empty_and_refused_responses_are_not_accepted(self):
        for kind in ("truncated", "empty", "refused", "oversized"):
            with self.subTest(kind=kind):
                choice = self.payload["choices"][0]
                choice["finish_reason"] = "length" if kind == "truncated" else "stop"
                choice["message"]["content"] = (
                    "x" * (provider.MAX_RESPONSE_BYTES + 1)
                    if kind == "oversized"
                    else ""
                )
                choice["message"]["refusal"] = "No" if kind == "refused" else None
                with self.assertRaises(GenerationError):
                    self.complete()
