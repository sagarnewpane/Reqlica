"""Private, per-project process environment. Public APIs return names, not values."""

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from reqlica.projects.drafts import _contained
from reqlica.projects.store import get_project
from reqlica.projects.versions import _fsync_directory, _read_code_file, project_lock

ENV_PATH = ".reqlica/environment.json"
EMPTY_REVISION = hashlib.sha256(b"reqlica-empty-project-environment-v1").hexdigest()
MAX_BYTES = 128 * 1024
RESERVED = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "PWD",
    "OLDPWD",
    "TMPDIR",
    "TEMP",
    "TMP",
    "VIRTUAL_ENV",
    "REQLICA_HOME",
    "GROQ_API_KEY",
    "GROQ_API",
}
RESERVED_PREFIXES = ("PYTHON", "LD_", "DYLD_", "MOCK_", "CONSOLE_", "REQLICA_UI_")


def validate_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        raise ValueError(
            "Use a variable name of 1–64 ASCII letters, digits, and underscores, not starting with a digit"
        )
    if name.upper() in RESERVED or name.upper().startswith(RESERVED_PREFIXES):
        raise ValueError(
            "That name is reserved for Reqlica or process startup settings"
        )
    return name


def _valid_values(values) -> dict[str, str]:
    if not isinstance(values, dict) or len(values) > 100:
        raise ValueError("Use at most 100 project variables")
    for name, value in values.items():
        validate_name(name)
        if (
            not isinstance(value, str)
            or not value
            or "\0" in value
            or len(value.encode("utf-8")) > 8192
        ):
            raise ValueError(
                "Variable values must be nonempty text without NUL, at most 8 KiB each"
            )
    return values


def read_environment(directory: Path) -> tuple[str, dict[str, str]]:
    """Atomic-read a private file; never put these values into source/context."""
    path = _contained(directory, ENV_PATH)
    if not path.exists():
        return EMPTY_REVISION, {}
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Environment configuration exceeds its size limit")
        raw = _read_code_file(directory, Path(ENV_PATH))
        if len(raw) > MAX_BYTES:
            raise ValueError("Environment configuration exceeds its size limit")
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "nonce", "values"}
            or data["version"] != 1
            or not isinstance(data["nonce"], str)
        ):
            raise ValueError("Invalid configuration")
        values = _valid_values(data["values"])
    except (ValueError, TypeError, RecursionError) as error:
        # JSON/validation errors can embed private values. Never echo them.
        raise ValueError(
            "Project environment configuration is invalid; repair the private settings file locally"
        ) from error
    return hashlib.sha256(raw).hexdigest(), values


def environment_metadata(directory: Path) -> dict:
    revision, values = read_environment(directory)
    return {
        "revision": revision,
        "names": sorted(values),
        "values_included": False,
        "storage": ENV_PATH,
    }


def get_environment(project_id: str) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        return {"project_id": project.id, **environment_metadata(project.directory)}


class EnvironmentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class EnvironmentWrite(EnvironmentRevision):
    value: SecretStr

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value):
        _valid_values({"VALUE": value.get_secret_value()})
        return value


def _write(directory: Path, values: dict[str, str]) -> None:
    _valid_values(values)
    content = json.dumps(
        {"version": 1, "nonce": uuid4().hex, "values": values}, ensure_ascii=False
    ).encode("utf-8")
    if len(content) > MAX_BYTES:
        raise ValueError("Project environment configuration exceeds 128 KiB")
    path = _contained(directory, ENV_PATH)
    temporary = _contained(directory, f".reqlica/environment-{uuid4().hex}.tmp")
    created = False
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def change_environment(
    project_id: str,
    name: str,
    expected_revision: str,
    value: SecretStr | None = None,
    *,
    generate: bool = False,
    remove: bool = False,
) -> dict:
    validate_name(name)
    project = get_project(project_id)
    with project_lock(project.directory):
        revision, values = read_environment(project.directory)
        if revision != expected_revision:
            raise ValueError(
                "Environment settings changed; reload variable names before retrying"
            )
        if remove:
            if name not in values:
                raise ValueError("Variable is not configured for this project")
            del values[name]
        else:
            if generate:
                values[name] = secrets.token_urlsafe(48)
            elif value is not None:
                values[name] = value.get_secret_value()
            else:
                raise ValueError(
                    "Supply a value or explicitly request secure generation"
                )
        _write(project.directory, values)
        return {
            "project_id": project.id,
            **environment_metadata(project.directory),
            "restart_required": True,
            "checks_require_rerun": True,
        }


def redact_output(text: str, values: dict[str, str]) -> str:
    # Best-effort defense for normal logs/errors, not a sandbox against Python
    # deliberately encoding/exfiltrating credentials in an API response.
    for value in sorted(values.values(), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text
