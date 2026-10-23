"""Persistent gateway response scenarios with independently locked request counters."""

import fcntl
import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from threading import Lock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from starlette.routing import compile_path
from reqlica.projects.drafts import _contained
from reqlica.projects.store import get_project
from reqlica.projects.versions import _checked_directory, _read_code_file, atomic_write_json

SCENARIO_PATH = ".reqlica/scenarios.json"
EMPTY_REVISION = "0" * 32
_lock = Lock()
MAX_RATE_BUCKETS = 1000


class RateLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests: int = Field(ge=1, le=1000000, strict=True)
    window_seconds: int = Field(ge=1, le=86400, strict=True)
    scope: Literal["route", "test_user"] = "route"


class RateBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reset_at: float = Field(gt=0, allow_inf_nan=False)
    count: int = Field(ge=1, le=1000000, strict=True)


class ScenarioRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"]
    path: str = Field(min_length=1, max_length=1024)
    delay_ms: int = Field(default=0, ge=0, le=30000, strict=True)
    response: Literal["passthrough", "failure", "empty"] = "passthrough"
    status_code: int = Field(default=503, ge=400, le=599, strict=True)
    body: JsonValue = Field(default_factory=lambda: {"detail": "simulated_failure"})
    failure_count: int | None = Field(default=None, ge=1, le=1000000, strict=True)
    rate_limit: RateLimit | None = None

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        if not value.startswith("/") or any(c in value for c in "?#\\") or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Use a registered route pattern without query or fragment")
        try:
            compile_path(value)
        except (AssertionError, ValueError) as error:
            raise ValueError("Invalid route pattern") from error
        return value

    @field_validator("body")
    @classmethod
    def bounded_body(cls, value):
        if len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError("Scenario response body exceeds 16 KiB")
        return value

    @model_validator(mode="after")
    def failure_counter_only(self):
        if self.response != "failure" and self.failure_count is not None:
            raise ValueError("failure_count requires a failure response")
        return self


class ScenarioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[ScenarioRule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_routes(self):
        keys = [(rule.method, rule.path) for rule in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("Only one scenario per method and route pattern")
        return self


class ScenarioWrite(ScenarioSettings):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{32}$")


class ScenarioState(ScenarioSettings):
    revision: str = Field(default=EMPTY_REVISION, pattern=r"^[0-9a-f]{32}$")
    remaining_failures: dict[str, int] = Field(default_factory=dict)
    rate_buckets: dict[str, RateBucket] = Field(default_factory=dict, max_length=MAX_RATE_BUCKETS)

    @model_validator(mode="after")
    def valid_counters(self):
        expected = {str(i): rule.failure_count for i, rule in enumerate(self.rules)
                    if rule.failure_count is not None}
        if set(expected) != set(self.remaining_failures) or any(
            type(count) is not int or not 0 <= count <= expected[key]
            for key, count in self.remaining_failures.items()
        ):
            raise ValueError("Invalid scenario counters")
        for key, bucket in self.rate_buckets.items():
            index, separator, identity = key.partition(":")
            if not separator or not index.isdecimal() or int(index) >= len(self.rules):
                raise ValueError("Invalid rate-limit bucket")
            limit = self.rules[int(index)].rate_limit
            if limit is None or bucket.count > limit.requests:
                raise ValueError("Invalid rate-limit count")
            if limit.scope == "route":
                valid_identity = identity == "route"
            else:
                valid_identity = len(identity) == 64 and all(c in "0123456789abcdef" for c in identity)
            if not valid_identity:
                raise ValueError("Invalid rate-limit identity")
        return self


@contextmanager
def _scenario_lock(directory):
    # Never acquire the project activation lock while a request lease is held:
    # activation may hold it while waiting for that request to finish.
    with _lock:
        _checked_directory(directory)
        state = _contained(directory, ".reqlica")
        state.mkdir(exist_ok=True)
        _checked_directory(state)
        descriptor = os.open(_contained(directory, ".reqlica/scenarios.lock"),
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _read(directory):
    path = _contained(directory, SCENARIO_PATH)
    if not path.exists():
        return ScenarioState()
    try:
        raw = _read_code_file(directory, path.relative_to(directory))
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Too large")
        return ScenarioState.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("Invalid scenario settings; repair the local configuration") from error


def _save(directory, state):
    payload = state.model_dump(mode="json")
    if len(json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False).encode()) > 2 * 1024 * 1024:
        raise ValueError("Scenario settings exceed 2 MiB")
    atomic_write_json(_contained(directory, SCENARIO_PATH), payload)


def _public(project_id, state):
    return {"project_id": project_id, "revision": state.revision, "rules": [
        {**rule.model_dump(mode="json"), "remaining_failures": state.remaining_failures.get(str(i))}
        for i, rule in enumerate(state.rules)]}


def get_scenarios(project_id):
    project = get_project(project_id)
    with _scenario_lock(project.directory):
        return _public(project_id, _read(project.directory))


def write_scenarios(project_id, body: ScenarioWrite):
    project = get_project(project_id)
    with _scenario_lock(project.directory):
        current = _read(project.directory)
        if body.expected_revision != current.revision:
            raise ValueError("Scenario settings changed; reload before retrying")
        state = ScenarioState(revision=uuid4().hex, rules=body.rules,
            remaining_failures={str(i): rule.failure_count for i, rule in enumerate(body.rules)
                                if rule.failure_count is not None})
        _save(project.directory, state)
        return _public(project_id, state)


def reset_scenario_counters(project_id, expected_revision):
    project = get_project(project_id)
    with _scenario_lock(project.directory):
        state = _read(project.directory)
        if state.revision != expected_revision:
            raise ValueError("Scenario settings changed; inspect them again")
        state.revision = uuid4().hex
        state.remaining_failures = {str(i): rule.failure_count for i, rule in enumerate(state.rules)
                                    if rule.failure_count is not None}
        state.rate_buckets = {}
        _save(project.directory, state)
        return _public(project_id, state)


def _reserve_rate(state, index, rule, headers, now):
    """Check quota before reserving a failure; all updates share the file lock."""
    limit = rule.rate_limit
    if limit is None:
        return None
    identity = "route"
    if limit.scope == "test_user":
        values = headers.getlist("X-Reqlica-Test-User") if headers is not None else []
        if len(values) != 1 or not 1 <= len(values[0]) <= 128 or any(not 33 <= ord(c) <= 126 for c in values[0]):
            return {"rate_limit_error": "invalid_test_user", "rate_limit_status": 400}
        identity = hashlib.sha256(values[0].encode()).hexdigest()
    # Discard expired identities to keep persistent state bounded.
    state.rate_buckets = {key: bucket for key, bucket in state.rate_buckets.items() if bucket.reset_at > now}
    key = f"{index}:{identity}"
    bucket = state.rate_buckets.get(key)
    if bucket is not None and bucket.count >= limit.requests:
        return {"rate_limit_error": "rate_limit_exceeded", "rate_limit_status": 429,
                "retry_after": max(1, math.ceil(bucket.reset_at - now))}
    if bucket is None:
        if len(state.rate_buckets) >= MAX_RATE_BUCKETS:
            return {"rate_limit_error": "rate_limit_capacity", "rate_limit_status": 429,
                    "retry_after": max(1, math.ceil(min(b.reset_at for b in state.rate_buckets.values()) - now))}
        state.rate_buckets[key] = RateBucket(reset_at=now + limit.window_seconds, count=1)
    else:
        bucket.count += 1
    return None


def consume_scenario(directory, method, path, manifest, headers=None):
    """Reserve a failure before delaying; cancellation still consumes the reservation."""
    if not _contained(directory, SCENARIO_PATH).exists():
        return None
    # Match the first registered route of this method, just like worker routing.
    route = next((endpoint.path for endpoint in manifest.endpoints
                  if endpoint.method == method and compile_path(endpoint.path)[0].match(path)), None)
    if route is None:
        return None
    with _scenario_lock(directory):
        state = _read(directory)
        for index, rule in enumerate(state.rules):
            if (rule.method, rule.path) != (method, route):
                continue
            rejection = _reserve_rate(state, index, rule, headers, time.time())
            if rejection is not None:
                return rejection
            action = rule.model_dump(mode="json")
            changed = rule.rate_limit is not None
            if rule.response == "failure" and rule.failure_count is not None:
                key = str(index)
                if not state.remaining_failures[key]:
                    action["response"] = "passthrough"
                else:
                    state.remaining_failures[key] -= 1
                    changed = True
            if changed:
                _save(directory, state)
            return action
    return None
