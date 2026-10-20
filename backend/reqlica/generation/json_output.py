"""Parse data/answer JSON without silently accepting duplicate object members."""

import json


def _unique_members(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys")
        result[key] = value
    return result


def reject_constant(value: str):
    raise ValueError("Non-finite numbers are not valid JSON")


def parse_json(text: str):
    return json.loads(
        text, object_pairs_hook=_unique_members, parse_constant=reject_constant
    )
