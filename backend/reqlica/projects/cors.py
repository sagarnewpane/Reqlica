"""Atomic per-project gateway CORS policy; runtime reads never take activation locks."""

import hashlib
import json
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from reqlica.projects.drafts import _contained
from reqlica.projects.store import get_project
from reqlica.projects.versions import _read_code_file, atomic_write_json, project_lock

CORS_PATH = ".reqlica/cors.json"
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"}
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")


class CorsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, strict=True)
    allow_origins: list[str] = Field(default_factory=list, max_length=100)
    allow_methods: list[str] = Field(default_factory=lambda: ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], max_length=8)
    allow_headers: list[str] = Field(default_factory=lambda: ["Content-Type", "Authorization", "X-API-Key", "X-Reqlica-Test-User"], max_length=100)
    expose_headers: list[str] = Field(default_factory=lambda: ["X-Reqlica-Request-ID", "X-Reqlica-Scenario", "Retry-After"], max_length=100)
    allow_credentials: bool = Field(default=False, strict=True)
    max_age: int = Field(default=600, ge=0, le=86400, strict=True)

    @field_validator("allow_origins")
    @classmethod
    def origins(cls, values):
        normalized = []
        for value in values:
            if value == "*":
                normalized.append(value)
                continue
            if len(value) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in value) or any(c in value for c in "\\?#"):
                raise ValueError("Use ASCII HTTP(S) origins without whitespace")
            try:
                parsed = urlsplit(value)
                port = parsed.port
                host = parsed.hostname
                if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment or value.endswith(":"):
                    raise ValueError("Invalid origin")
                host = f"[{host}]" if ":" in host else host
                origin = f"{parsed.scheme}://{host}"
                if port is not None and port != (443 if parsed.scheme == "https" else 80):
                    origin += f":{port}"
                normalized.append(origin)
            except ValueError as error:
                raise ValueError("Use HTTP(S) scheme/host/port origins without paths, queries or credentials") from error
        if len(set(normalized)) != len(normalized) or ("*" in normalized and len(normalized) != 1):
            raise ValueError("Use unique origins or a single wildcard")
        return normalized

    @field_validator("allow_methods")
    @classmethod
    def methods(cls, values):
        if not values or len(set(values)) != len(values) or not set(values) <= _METHODS:
            raise ValueError("Use unique uppercase HTTP methods")
        return values

    @field_validator("allow_headers", "expose_headers")
    @classmethod
    def headers(cls, values):
        if len({v.lower() for v in values}) != len(values) or any(not _TOKEN.fullmatch(v) for v in values):
            raise ValueError("Use unique HTTP header names")
        if "*" in values and len(values) != 1:
            raise ValueError("Use explicit headers or a single wildcard")
        return values

    @model_validator(mode="after")
    def credential_policy(self):
        if self.enabled and not self.allow_origins:
            raise ValueError("Enabled CORS requires allowed origins")
        if self.allow_credentials and any("*" in values for values in (self.allow_origins, self.allow_headers, self.expose_headers)):
            raise ValueError("Credentialed CORS requires explicit origins and headers")
        if len(json.dumps(self.model_dump(exclude={"expected_revision"}), indent=2).encode()) > 65536:
            raise ValueError("CORS settings exceed 64 KiB")
        return self


class CorsWrite(CorsSettings):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def read_cors(directory):
    path = _contained(directory, CORS_PATH)
    if not path.exists():
        return hashlib.sha256(b"{}").hexdigest(), CorsSettings()
    try:
        raw = _read_code_file(directory, path.relative_to(directory))
        if len(raw) > 65536:
            raise ValueError("Too large")
        settings = CorsSettings.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("Invalid project CORS settings") from error
    return hashlib.sha256(raw).hexdigest(), settings


def get_cors(project_id):
    project = get_project(project_id)
    with project_lock(project.directory):
        revision, settings = read_cors(project.directory)
        return {"project_id":project_id, "revision":revision, **settings.model_dump()}


def write_cors(project_id, body: CorsWrite):
    project = get_project(project_id)
    with project_lock(project.directory):
        revision, _ = read_cors(project.directory)
        if revision != body.expected_revision:
            raise ValueError("CORS settings changed; reload before retrying")
        settings = CorsSettings.model_validate(body.model_dump(exclude={"expected_revision"}))
        atomic_write_json(_contained(project.directory, CORS_PATH), settings.model_dump())
        return get_cors(project_id)


def runtime_cors(project_id):
    return read_cors(get_project(project_id).directory)[1]
