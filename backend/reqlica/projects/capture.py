"""Explicit, bounded payload/output capture with secret-key and environment redaction."""

import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field
from reqlica.projects.drafts import _contained
from reqlica.projects.environment import read_environment, redact_output
from reqlica.projects.store import get_project
from reqlica.projects.versions import atomic_write_json, project_lock


class CaptureSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_payloads: bool = Field(default=False, strict=True)
    capture_output: bool = Field(default=False, strict=True)
    max_body_bytes: int = Field(default=1024, ge=128, le=2048, strict=True)


class CaptureWrite(CaptureSettings):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def read_capture(directory):
    path = _contained(directory, ".reqlica/inspection.json")
    raw = path.read_bytes() if path.exists() else b"{}"
    if len(raw) > 4096:
        raise ValueError("Invalid capture settings")
    return hashlib.sha256(raw).hexdigest(), CaptureSettings.model_validate_json(raw)


def get_capture(project_id):
    project = get_project(project_id)
    revision, config = read_capture(project.directory)
    return {"revision":revision, **config.model_dump()}


def write_capture(project_id, body):
    project = get_project(project_id)
    with project_lock(project.directory):
        revision, _ = read_capture(project.directory)
        if revision != body.expected_revision:
            raise ValueError("Capture settings changed; reload them")
        atomic_write_json(_contained(project.directory, ".reqlica/inspection.json"), body.model_dump(exclude={"expected_revision"}))
        return get_capture(project_id)


def bounded_payload(raw, limit, values):
    if raw is None:
        return None
    def scrub(value):
        if isinstance(value, dict):
            return {key: "[REDACTED]" if any(word in key.lower().replace("-", "").replace("_", "") for word in ("password", "secret", "token", "authorization", "cookie", "apikey")) else scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            return redact_output(value, values)
        return value
    if len(raw) <= 65536:
        try:
            text = json.dumps(scrub(json.loads(raw)), ensure_ascii=True)
        except (ValueError, UnicodeDecodeError, RecursionError):
            text = raw[:limit].decode("utf-8", errors="replace")
    else:
        return {"omitted":"body exceeds capture parsing limit", "bytes":len(raw)}
    text = redact_output(text, values)
    encoded = text.encode("utf-8")
    return {"text":encoded[:limit].decode("utf-8", errors="ignore"), "truncated":len(encoded) > limit, "bytes":len(raw)}


def capture_request(directory, event, request_body, response_body):
    try:
        _, config = read_capture(directory)
        if config.capture_payloads:
            _, values = read_environment(directory)
            event["request_body"] = bounded_payload(request_body, config.max_body_bytes, values)
            event["response_body"] = bounded_payload(response_body, config.max_body_bytes, values)
    except (OSError, ValueError):
        pass


class OutputCapture:
    def __init__(self, original, directory, kind, values):
        self.original, self.directory, self.kind, self.values = original, directory, kind, values

    def __getattr__(self, name):
        return getattr(self.original, name)

    def write(self, text):
        result = self.original.write(text)
        try:
            _, config = read_capture(self.directory)
            if config.capture_output and text.strip():
                from reqlica.projects.inspection import record_event
                clean = redact_output(text, self.values).encode()[:2048].decode("utf-8", errors="ignore")
                record_event(self.directory, {"kind":self.kind, "text":clean, "truncated":len(text.encode()) > 2048})
        except (OSError, ValueError):
            pass
        return result

    def flush(self):
        return self.original.flush()
