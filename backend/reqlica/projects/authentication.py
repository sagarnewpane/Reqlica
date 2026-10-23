"""Gateway-only mock credentials, independent of generated application auth."""

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from reqlica.projects.drafts import _contained
from reqlica.projects.store import get_project
from reqlica.projects.versions import _read_code_file, atomic_write_json, project_lock

AUTH_PATH = ".reqlica/mock-auth.json"


class CredentialMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    expires_at: AwareDatetime | None = None


class Credential(CredentialMetadata):
    value: SecretStr

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        text = value.get_secret_value()
        if not 1 <= len(text) <= 256 or any(not 33 <= ord(c) <= 126 for c in text):
            raise ValueError("Use 1–256 printable ASCII characters without spaces")
        return value


class StoredCredential(CredentialMetadata):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "bearer", "api_key"] = "none"
    header_name: str = Field(default="X-API-Key", pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")

    @field_validator("header_name")
    @classmethod
    def safe_header(cls, value):
        if value.lower() in {"authorization", "host", "content-length", "content-type", "connection", "cookie", "transfer-encoding", "upgrade"} or value.lower().startswith(("proxy-", "sec-", "x-forwarded-")):
            raise ValueError("Choose a dedicated API-key header")
        return value


class StoredAuth(AuthSettings):
    credentials: list[StoredCredential] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_credentials(self):
        if self.mode != "none" and not self.credentials:
            raise ValueError("Enabled authentication requires credentials")
        if len({item.name for item in self.credentials}) != len(self.credentials) or len({item.digest for item in self.credentials}) != len(self.credentials):
            raise ValueError("Credential names and values must be unique")
        return self


class AuthWrite(AuthSettings):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    credentials: list[Credential] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_credentials(self):
        names = [item.name for item in self.credentials]
        values = [item.value.get_secret_value() for item in self.credentials]
        if len(set(names)) != len(names) or len(set(values)) != len(values):
            raise ValueError("Credential names and values must be unique")
        if self.mode != "none" and not self.credentials:
            raise ValueError("Enabled authentication requires credentials")
        return self


def _read(directory):
    path = _contained(directory, AUTH_PATH)
    if not path.exists():
        raw = b"{}"
        config = StoredAuth()
    else:
        try:
            raw = _read_code_file(directory, path.relative_to(directory))
            if len(raw) > 65536:
                raise ValueError("Too large")
            config = StoredAuth.model_validate_json(raw)
        except ValueError as error:
            raise ValueError("Invalid mock authentication settings; repair the local configuration") from error
    return hashlib.sha256(raw).hexdigest(), config


def _public(project_id, revision, config):
    return {"project_id": project_id, "revision": revision, "mode": config.mode,
            "header_name": config.header_name,
            "credentials": [CredentialMetadata.model_validate(item.model_dump(exclude={"digest"})).model_dump(mode="json") for item in config.credentials]}


def get_authentication(project_id):
    project = get_project(project_id)
    with project_lock(project.directory):
        revision, config = _read(project.directory)
        return _public(project_id, revision, config)


def write_authentication(project_id, body: AuthWrite):
    project = get_project(project_id)
    with project_lock(project.directory):
        revision, _ = _read(project.directory)
        if body.expected_revision != revision:
            raise ValueError("Mock authentication changed; reload before retrying")
        config = StoredAuth(mode=body.mode, header_name=body.header_name, credentials=[
            StoredCredential(name=item.name, expires_at=item.expires_at,
                digest=hashlib.sha256(item.value.get_secret_value().encode()).hexdigest())
            for item in body.credentials])
        atomic_write_json(_contained(project.directory, AUTH_PATH), config.model_dump(mode="json"))
        return get_authentication(project_id)


def authentication_error(directory, headers):
    """Return a bounded failure; never echo supplied credentials. No handler imports."""
    # Atomic replacement gives a complete snapshot without blocking activation.
    _, config = _read(directory)
    if config.mode == "none":
        return None
    challenge = {"WWW-Authenticate": "Bearer"} if config.mode == "bearer" else {}
    header = "authorization" if config.mode == "bearer" else config.header_name
    values = headers.getlist(header)
    if not values:
        return "missing_credentials", challenge
    value = values[0]
    if len(values) != 1:
        return "invalid_credentials", challenge
    if config.mode == "bearer":
        scheme, separator, value = value.partition(" ")
        if scheme.lower() != "bearer" or not separator:
            return "invalid_credentials", challenge
    digest = hashlib.sha256(value.encode()).hexdigest()
    for credential in config.credentials:
        if hmac.compare_digest(digest, credential.digest):
            if credential.expires_at is not None and credential.expires_at <= datetime.now(timezone.utc):
                return "expired_credentials", challenge
            return None
    return "invalid_credentials", challenge
