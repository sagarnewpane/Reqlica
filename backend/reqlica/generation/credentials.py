"""Write-only, project-scoped provider keys kept outside source and exports."""

import hashlib
import json
import re
from pathlib import Path

from pydantic import SecretStr, field_validator
from reqlica.generation.schemas import ProviderEndpoint
from reqlica.projects.store import get_project, registry_directory
from reqlica.projects.versions import (
    _checked_directory, _read_code_file, atomic_write_json, project_lock,
)


class ProviderKeyWrite(ProviderEndpoint):
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        text = value.get_secret_value()
        if not text.strip() or len(text.encode()) > 8192 or any(c in text for c in "\r\n\0"):
            raise ValueError("Use a nonempty, single-line API key of at most 8 KiB")
        return SecretStr(text.strip())


def _scope(endpoint: ProviderEndpoint) -> str:
    url = endpoint.base_url or ""
    if endpoint.provider in {"groq", "openai"}:
        url = ""  # ProviderEndpoint permits only the official URL or its default.
    elif endpoint.provider == "ollama":
        url = url or "http://localhost:11434"
    return hashlib.sha256(json.dumps([endpoint.provider, url]).encode()).hexdigest()


def _path(project_id: str) -> Path:
    directory = registry_directory().parent / "provider-keys"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = _checked_directory(directory)
    directory.chmod(0o700)
    return directory / f"{project_id}.json"


def _read(path: Path) -> dict[str, str]:
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        if path.lstat().st_size > 128 * 1024:
            raise ValueError("Too large")
        data = json.loads(_read_code_file(path.parent, Path(path.name)))
        if not isinstance(data, dict) or len(data) > 64:
            raise ValueError("Invalid entries")
        for scope, key in data.items():
            if (not re.fullmatch(r"[a-f0-9]{64}", scope) or not isinstance(key, str)
                    or not key or len(key.encode()) > 8192 or any(c in key for c in "\r\n\0")):
                raise ValueError("Invalid entry")
        return data
    except (ValueError, OSError, TypeError, RecursionError):
        raise ValueError("Provider key configuration could not be read. Repair the private settings file locally.") from None


def key_status(project_id: str, endpoint: ProviderEndpoint) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        return {"configured": _scope(endpoint) in _read(_path(project.id))}


def save_key(project_id: str, endpoint: ProviderEndpoint, key: SecretStr | None) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        path = _path(project.id)
        values = _read(path)
        scope = _scope(endpoint)
        if key is None:
            values.pop(scope, None)
        else:
            values[scope] = key.get_secret_value()
        if len(values) > 64 or len(json.dumps(values).encode()) > 128 * 1024:
            raise ValueError("Too many saved provider keys for this project")
        atomic_write_json(path, values)  # mkstemp creates an owner-only (0600) file.
        return {"configured": scope in values}


def get_key(project_id: str, endpoint: ProviderEndpoint) -> SecretStr | None:
    project = get_project(project_id)
    with project_lock(project.directory):
        value = _read(_path(project.id)).get(_scope(endpoint))
        return SecretStr(value) if value else None
