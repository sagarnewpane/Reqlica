"""Bounded, ephemeral AI activity. Never retain prompts, keys or model content."""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from time import monotonic

_current = ContextVar("reqlica_ai_activity", default=None)
_activities = OrderedDict()
_lock = RLock()
TTL_SECONDS = 900
MAX_ACTIVITIES = 128
MAX_EVENTS = 64
STAGES = {"received", "provider_request", "provider_accepted", "provider_rejected",
          "provider_response", "checking", "completed", "failed"}


def _prune():
    cutoff = monotonic() - TTL_SECONDS
    for key in list(_activities):
        if _activities[key]["started"] < cutoff:
            del _activities[key]


def report(stage: str):
    """Only fixed stage names cross the diagnostics boundary."""
    key = _current.get()
    if key is None or stage not in STAGES:
        return
    with _lock:
        activity = _activities.get(key)
        if activity is None:
            return
        activity["events"].append({
            "stage": stage,
            "elapsed_ms": round((monotonic() - activity["started"]) * 1000),
        })
        activity["events"] = activity["events"][-MAX_EVENTS:]


@contextmanager
def track(project_id: str, activity_id: str | None):
    if activity_id is None:
        yield
        return
    key = (project_id, activity_id)
    with _lock:
        _prune()
        if key in _activities:
            raise ValueError("Activity ID already used; create a new request ID")
        while len(_activities) >= MAX_ACTIVITIES:
            _activities.popitem(last=False)
        _activities[key] = {"started": monotonic(), "events": []}
    token = _current.set(key)
    report("received")
    try:
        yield
    except Exception:
        report("failed")
        raise
    else:
        report("completed")
    finally:
        _current.reset(token)


def inspect(project_id: str, activity_id: str) -> dict:
    with _lock:
        _prune()
        activity = _activities.get((project_id, activity_id))
        if activity is None:
            raise FileNotFoundError("Activity not found or expired")
        return {"events": [dict(event) for event in activity["events"]]}
