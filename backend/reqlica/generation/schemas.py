"""Single-turn generation inputs and the model's strictly structured proposal."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from reqlica.projects.store import Endpoint


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["openai/gpt-oss-20b", "openai/gpt-oss-120b"] = "openai/gpt-oss-20b"
    temperature: float = Field(default=0.2, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=8192, ge=256, le=32768)


class GenerationRequest(ModelSettings):
    requirements: str = Field(min_length=1, max_length=32_768)

    @field_validator("requirements")
    @classmethod
    def nonblank_requirements(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Describe the endpoints to generate")
        return value


class ProjectQuestionRequest(ModelSettings):
    question: str = Field(min_length=1, max_length=16_384)
    max_tokens: int = Field(default=4096, ge=256, le=32768)

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Ask a question about this project")
        return value


class ProjectAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str
    sources: list[str]
    limitations: list[str]


class GeneratedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    content: str


class GeneratedPlan(BaseModel):
    # Keep every field required for Groq's strict JSON-schema output mode.
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str
    assumptions: list[str]
    files: list[GeneratedFile] = Field(
        description="ONE combined array of every handler, model, and test file. Never repeat the files key."
    )
    endpoints: list[Endpoint] = Field(
        description="ONE array containing all route definitions to add or update."
    )
