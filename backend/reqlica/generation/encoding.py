"""Stable, compact JSON for model context; preserve text and sequence order."""

import json


def prompt_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
