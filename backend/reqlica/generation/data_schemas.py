"""Data previews and explicit, append-only import contracts."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


class DataTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["json", "sqlite"] = "json"
    table: str | None = Field(default=None, min_length=1, max_length=128)
    expected_schema: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def selected_table(self):
        if self.target == "sqlite" and (not self.table or not self.expected_schema):
            raise ValueError(
                "Select an existing table and its current schema fingerprint"
            )
        if self.target == "json" and (
            self.table is not None or self.expected_schema is not None
        ):
            raise ValueError("JSON fixtures do not select or modify a database table")
        return self






class DataValidationRequest(DataTarget):
    records: list[dict[str, JsonValue]] = Field(min_length=1, max_length=100)


class DataImportRequest(DataValidationRequest):
    expected_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value

    @model_validator(mode="after")
    def database_only(self):
        if self.target != "sqlite":
            raise ValueError("Only SQLite previews can be inserted")
        return self


class RelatedDataValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_schema: str = Field(pattern=r"^[0-9a-f]{64}$")
    records_by_table: dict[str, list[dict[str, JsonValue]]]


class RelatedDataImportRequest(RelatedDataValidationRequest):
    expected_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value
