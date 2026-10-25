"""Shared deterministic source and provider transport fixtures for Project AI."""
import os
from unittest.mock import patch
import httpx
from openai import OpenAI
from pydantic import SecretStr
from reqlica.generation import provider
from reqlica.generation.schemas import AgentRequest

HANDLER = """from pydantic import BaseModel

class Greeting(BaseModel):
    message: str

def greet(name: str = "reader") -> Greeting:
    return Greeting(message="Hello, " + name)
"""
TEST_SOURCE = """import unittest
from endpoints.greetings import greet

class GreetingTests(unittest.TestCase):
    def test_named_reader(self):
        self.assertEqual(greet("Ada").message, "Hello, Ada")

    def test_default_reader(self):
        self.assertEqual(greet().message, "Hello, reader")
"""

class ProviderFixture:
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.request = AgentRequest(message="Create an endpoint", model="test-tool-model")
        self.messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "context"},
        ]
        self.requests = []
        self.options = {}
        self.status = 200
        self.exception = None
        self.payload = {
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": self.request.model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "A concise assistant reply"},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

        def handle(request):
            self.requests.append(request)
            if self.exception:
                raise self.exception("network failure", request=request)
            return httpx.Response(self.status, json=self.payload)

        def factory(**kwargs):
            self.options = kwargs.copy()
            kwargs["http_client"] = httpx.Client(
                transport=httpx.MockTransport(handle), trust_env=False
            )
            return OpenAI(**kwargs)

        self.enterContext(
            patch("reqlica.generation.provider.OpenAI", side_effect=factory)
        )

        self.enterContext(patch("reqlica.generation.provider.openrouter_model", return_value={"supported_parameters": ["tools"]}))

    def complete(self):
        return provider.complete(self.messages, self.request, SecretStr("private-provider-key"), tools=[])
