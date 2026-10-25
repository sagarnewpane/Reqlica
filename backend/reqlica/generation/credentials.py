"""App-wide provider keys kept outside project source and exports."""

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


def _path(project_id: str | None = None) -> Path:
    directory = registry_directory().parent / "provider-keys"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = _checked_directory(directory)
    directory.chmod(0o700)
    return directory / "application.json"


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


def _values(path: Path) -> dict[str, str]:
    """Import unambiguous legacy keys once; never choose between conflicting keys."""
    if path.exists() or path.is_symlink():
        return _read(path)
    candidates: dict[str, set[str]] = {}
    for legacy in sorted(path.parent.glob("*.json")):
        if not re.fullmatch(r"[a-f0-9]{32}\.json", legacy.name):
            continue
        for scope, value in _read(legacy).items():
            candidates.setdefault(scope, set()).add(value)
    conflicts = [scope for scope, values in candidates.items() if len(values) > 1]
    values = {scope: next(iter(keys)) for scope, keys in candidates.items() if len(keys) == 1}
    if len(values) > 64 or len(json.dumps(values).encode()) > 128 * 1024 or len(json.dumps(conflicts)) > 8192:
        raise ValueError("Legacy provider keys exceed app settings limits; consolidate the private legacy files first")
    atomic_write_json(path.parent / "migration-conflicts.json", conflicts)
    atomic_write_json(path, values)
    return values


def _conflicts(path: Path) -> list[str]:
    marker = path.parent / "migration-conflicts.json"
    if not marker.exists() and not marker.is_symlink():
        return []
    if marker.lstat().st_size > 8192:
        raise ValueError("Provider key migration metadata exceeds its size limit")
    values = json.loads(_read_code_file(marker.parent, Path(marker.name)))
    if not isinstance(values, list) or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in values):
        raise ValueError("Invalid provider key migration metadata")
    return values


def _validate_project(project_id):
    # Keep old project endpoints compatible; credentials are no longer scoped by it.
    if project_id is not None:
        get_project(project_id)


def key_status(project_id: str | None, endpoint: ProviderEndpoint) -> dict:
    _validate_project(project_id)
    path = _path()
    with project_lock(path.parent):
        configured = _scope(endpoint) in _values(path)
        return {"configured": configured} | (
            {"migration_conflict": True} if not configured and _scope(endpoint) in _conflicts(path) else {})


def save_key(project_id: str | None, endpoint: ProviderEndpoint, key: SecretStr | None) -> dict:
    _validate_project(project_id)
    path = _path()
    with project_lock(path.parent):
        values = _values(path)
        scope = _scope(endpoint)
        if key is None:
            values.pop(scope, None)
        else:
            values[scope] = key.get_secret_value()
        if len(values) > 64 or len(json.dumps(values).encode()) > 128 * 1024:
            raise ValueError("Too many saved provider keys for this app")
        atomic_write_json(path, values)
        atomic_write_json(path.parent / "migration-conflicts.json", [value for value in _conflicts(path) if value != scope])
        return {"configured": scope in values}


def get_key(project_id: str | None, endpoint: ProviderEndpoint) -> SecretStr | None:
    _validate_project(project_id)
    path = _path()
    with project_lock(path.parent):
        value = _values(path).get(_scope(endpoint))
        return SecretStr(value) if value else None
