"""Typed AI requests, bounded conversation exchanges, and structured model output."""

import json
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from reqlica.projects.store import Endpoint


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["groq", "openai", "compatible"] = "groq"
    base_url: str | None = Field(default=None, max_length=2048)
    model: str = Field(default="openai/gpt-oss-20b", min_length=1, max_length=200)

    @model_validator(mode="after")
    def provider_settings(self):
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("Enter a model identifier")
        if self.base_url:
            url = urlsplit(self.base_url.strip())
            if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
                raise ValueError("Use HTTPS or loopback HTTP without URL credentials, query or fragment")
            path = url.path.rstrip("/")
            if path.endswith("/chat/completions"):
                path = path[:-len("/chat/completions")]
            self.base_url = urlunsplit((url.scheme, url.netloc, path, "", ""))
        if self.provider == "groq":
            if self.base_url not in {None, "https://api.groq.com/openai/v1"}:
                raise ValueError("A Groq profile must use the Groq base URL")
            if self.model not in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
                raise ValueError("Select a supported Groq GPT-OSS model")
        elif self.provider == "compatible" and not self.base_url:
            raise ValueError("An OpenAI-compatible profile requires a base URL")
        elif self.provider == "openai" and self.base_url not in {None, "https://api.openai.com/v1"}:
            raise ValueError("Use an OpenAI-compatible profile for a custom base URL")
        return self
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=8192, ge=256, le=32768)
    timeout_seconds: int = Field(default=300, ge=30, le=600, strict=True)
    reasoning_mode: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"] = "default"


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    kind: Literal["field", "environment", "behavior"]
    question: str = Field(min_length=1, max_length=1024, pattern=r"\S")
    options: list[
        Annotated[str, Field(min_length=1, max_length=512, pattern=r"\S")]
    ] = Field(max_length=6)


class ClarificationReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1, max_length=8192, pattern=r"\S")
    questions: list[ClarificationQuestion] = Field(max_length=8)


class ClarificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: ClarificationQuestion
    answer: str = Field(min_length=1, max_length=4096)

    @field_validator("answer")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Answer each clarification before generating")
        return value.strip()


class ClarificationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: list[ClarificationDecision] = Field(max_length=8)

    @model_validator(mode="after")
    def distinct_decisions(self):
        ids = [decision.question.id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate clarification decisions")
        return self


class GenerationRequest(ModelSettings):
    requirements: str = Field(min_length=1, max_length=32_768)
    clarification: ClarificationSubmission | None = None

    @field_validator("requirements")
    @classmethod
    def nonblank_requirements(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Describe the endpoints to generate")
        return value


class EndpointEditRequest(GenerationRequest):
    endpoint: Endpoint
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentRequest(ModelSettings):
    message: str = Field(min_length=1, max_length=32_768, pattern=r"\S")
    draft_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    allow_execution: bool = False
    allow_edits: bool = True

    @model_validator(mode="after")
    def selected_revision(self):
        if bool(self.draft_id) != bool(self.expected_revision):
            raise ValueError("Select both the draft and its current revision, or neither for a new draft")
        return self


class AgentReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=32_768, pattern=r"\S")
    reply_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_turn: int = Field(ge=0, strict=True)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class RepairRequest(ModelSettings):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: bool = False
    requirements: str | None = Field(default=None, min_length=1, max_length=32_768)


class RepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1, max_length=8192, pattern=r"\S")
    files: list["GeneratedFile"] = Field(max_length=10)
    blockers: list[
        Annotated[str, Field(min_length=1, max_length=2048, pattern=r"\S")]
    ] = Field(max_length=8)


class ClarificationRequest(GenerationRequest):
    endpoint: Endpoint | None = None
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def edit_revision(self):
        if (self.endpoint is None) != (self.expected_revision is None):
            raise ValueError(
                "Endpoint reviews require both the selected route and its revision"
            )
        return self


MAX_HISTORY_TURNS = 8
MAX_HISTORY_BYTES = 48 * 1024


class ConversationTurn(BaseModel):
    """A complete prior exchange, not arbitrary system/tool/assistant messages."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=16_384)
    answer: str = Field(min_length=1, max_length=32_768)
    context_revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("question", "answer")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Conversation messages cannot be blank")
        return value


class ProjectQuestionRequest(ModelSettings):
    question: str = Field(min_length=1, max_length=16_384)
    max_tokens: int = Field(default=4096, ge=256, le=32768)
    history: list[ConversationTurn] = Field(
        default_factory=list, max_length=MAX_HISTORY_TURNS
    )

    @model_validator(mode="after")
    def bounded_history(self):
        encoded = json.dumps(
            [turn.model_dump() for turn in self.history],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_HISTORY_BYTES:
            raise ValueError("Conversation history exceeds the 48 KiB limit")
        return self

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Ask a question about this project")
        return value


class ProjectAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str = Field(min_length=1, max_length=32_768, pattern=r"\S")
    sources: list[str] = Field(max_length=50)
    limitations: list[Annotated[str, Field(max_length=4096)]] = Field(max_length=20)


class GeneratedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    content: str


class GeneratedPlan(BaseModel):
    # Keep every field required for Groq's strict JSON-schema output mode.
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(min_length=1, max_length=8192, pattern=r"\S")
    assumptions: list[Annotated[str, Field(max_length=4096)]] = Field(max_length=50)
    files: list[GeneratedFile] = Field(
        max_length=20,
        description="ONE combined array of every handler, model, and test file. Never repeat the files key."
    )
    endpoints: list[Endpoint] = Field(
        max_length=50,
        description="ONE array containing all route definitions to add or update."
    )


class PlannedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    instructions: str = Field(min_length=1, max_length=8192)


class GenerationOutline(BaseModel):
    """Small wire contract for a resumable generation, without embedded source."""
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1, max_length=8192, pattern=r"\S")
    assumptions: list[Annotated[str, Field(max_length=4096)]] = Field(max_length=50)
    interfaces: str = Field(max_length=16384, description="Shared function signatures, models, imports, database tables and HTTP contracts.")
    files: list[PlannedFile] = Field(max_length=20)
    endpoints: list[Endpoint] = Field(max_length=50)


class FileContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(min_length=1, max_length=65536)
