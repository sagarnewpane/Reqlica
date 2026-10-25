"""Provider settings and the unified project conversation contracts."""

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MODEL_CALL_LIMIT = 24
DEFAULT_PER_TURN_MODEL_CALL_LIMIT = 16


class ProviderEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["groq", "openai", "compatible", "ollama"] = "groq"
    base_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def provider_settings(self):
        if self.base_url:
            url = urlsplit(self.base_url.strip())
            if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
                raise ValueError("Use HTTPS or loopback HTTP without URL credentials, query or fragment")
            path = url.path.rstrip("/")
            if path.endswith("/chat/completions"):
                path = path[:-len("/chat/completions")]
            if self.provider == "ollama" and path.endswith(("/api", "/v1")):
                path = path.rsplit("/", 1)[0]
            self.base_url = urlunsplit((url.scheme, url.netloc, path, "", ""))
        if self.provider == "groq":
            if self.base_url not in {None, "https://api.groq.com/openai/v1"}:
                raise ValueError("A Groq profile must use the Groq base URL")
        elif self.provider == "compatible" and not self.base_url:
            raise ValueError("An OpenAI-compatible profile requires a base URL")
        elif self.provider == "openai" and self.base_url not in {None, "https://api.openai.com/v1"}:
            raise ValueError("Use an OpenAI-compatible profile for a custom base URL")
        return self


class ModelSettings(ProviderEndpoint):
    model: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def selected_model(self):
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("Enter a model identifier")
        return self
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=8192, ge=256, le=65536)
    timeout_seconds: int = Field(default=300, ge=30, le=600, strict=True)
    reasoning_mode: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"] = "off"


from reqlica.generation.specifications import Specification


class AgentRequest(ModelSettings):
    specification: Specification | None = None
    total_token_limit: int = Field(default=100000, ge=1000, le=1000000, strict=True)
    tool_call_limit: int = Field(default=64, ge=1, le=1000, strict=True)
    model_call_limit: int = Field(default=DEFAULT_MODEL_CALL_LIMIT, ge=1, le=1000, strict=True)
    per_turn_model_call_limit: int = Field(default=DEFAULT_PER_TURN_MODEL_CALL_LIMIT, ge=1, le=1000, strict=True)
    message: str = Field(min_length=1, max_length=32_768, pattern=r"\S")
    draft_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    allow_execution: bool = False
    allow_edits: bool = True

    @model_validator(mode="after")
    def model_call_budgets(self):
        if self.per_turn_model_call_limit > self.model_call_limit:
            raise ValueError("Per-turn model calls cannot exceed the conversation model-call limit")
        return self

    @model_validator(mode="after")
    def selected_revision(self):
        if bool(self.draft_id) != bool(self.expected_revision):
            raise ValueError("Select both the draft and its current revision, or neither for a new draft")
        if self.specification and not self.allow_edits:
            raise ValueError("Specification import requires draft editing permission")
        return self


class AgentReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=32_768, pattern=r"\S")
    reply_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_turn: int = Field(ge=0, strict=True)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentLimitsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_total_token_limit: int = Field(ge=1000, le=1000000, strict=True)
    expected_tool_call_limit: int = Field(ge=1, le=1000, strict=True)
    expected_model_call_limit: int = Field(ge=1, le=1000, strict=True)
    expected_per_turn_model_call_limit: int = Field(ge=1, le=1000, strict=True)
    total_token_limit: int = Field(ge=1000, le=1000000, strict=True)
    tool_call_limit: int = Field(ge=1, le=1000, strict=True)
    model_call_limit: int = Field(ge=1, le=1000, strict=True)
    per_turn_model_call_limit: int = Field(ge=1, le=1000, strict=True)

    @model_validator(mode="after")
    def model_call_budgets(self):
        if self.per_turn_model_call_limit > self.model_call_limit:
            raise ValueError("Per-turn model calls cannot exceed the conversation model-call limit")
        return self


class AgentReasoningResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_reasoning_mode: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"]


class AgentReasoningChangeRequest(AgentReasoningResetRequest):
    reasoning_mode: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"]
