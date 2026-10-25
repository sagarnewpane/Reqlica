"""Compact the model-facing representation without summarizing conversation facts."""

from copy import deepcopy
import json
import re

from reqlica.generation.json_output import parse_json

_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SCHEMA_VALUES = {"items", "additionalProperties", "unevaluatedProperties", "propertyNames", "contains", "not", "if", "then", "else"}
_SCHEMA_ARRAYS = {"anyOf", "allOf", "oneOf", "prefixItems"}
_COMPACT_RESULTS = {"create_draft", "write_draft_file", "upsert_endpoint", "remove_endpoint",
                    "validate_draft", "test_draft", "call_draft_endpoint"}
OMITTED_SOURCE_MARKERS = frozenset({
    "[Saved source in the tool-evidence ledger.]",
    "[Earlier source omitted from model context; read the saved file with get_project_context if needed.]",
})


def compact_tool_feedback(payload):
    """Keep the facts needed for repair; omit copied draft state and long logs."""
    if not isinstance(payload, dict):
        return payload
    keep = ("id", "revision", "status", "code", "path", "line", "field",
            "saved", "next_step", "source_location", "registered_routes", "functions",
            "tests_run", "tests_passed", "tests_expected_failures", "status_code", "recovered",
            "column", "type", "loc", "recovery")
    compact = {key: payload[key] for key in keep if key in payload}
    seen = set()
    for key in ("error", "message", "agent_feedback"):
        if isinstance(payload.get(key), str):
            # Pydantic text embeds submitted input and a documentation URL for
            # every issue. Locations and causes suffice for correcting a call.
            value = re.sub(r", input_value=.*", "]", payload[key])
            value = "\n".join(line for line in value.splitlines() if "https://errors.pydantic.dev/" not in line)
            value = value[:1600]
            if value not in seen:
                compact[key] = value
                seen.add(value)
    if isinstance(payload.get("output"), str):
        compact["output"] = payload["output"][-2400:]
    if isinstance(payload.get("body"), str):
        compact["body"] = payload["body"][:2000]
    if isinstance(payload.get("failures"), list):
        compact["failures"] = [
            {key: (value[-900:] if isinstance(value, str) else value)
             for key, value in item.items() if key in {"test", "output", "message", "code"}}
            for item in payload["failures"][:3] if isinstance(item, dict)
        ]
        if len(payload["failures"]) > 3:
            compact["more_failures"] = len(payload["failures"]) - 3
    if isinstance(payload.get("static_check"), dict):
        compact["static_check"] = compact_tool_feedback(payload["static_check"])
    if isinstance(payload.get("schema"), dict):
        compact["schema"] = {key: payload["schema"][key] for key in
                             ("status", "warning_count") if key in payload["schema"]}
    for key in ("response", "result"):
        if key in payload:
            value = json.dumps(payload[key], ensure_ascii=False, default=str)
            compact[key] = value[:2000]
    # Validators return structured issues, not only a text log. Preserve locations
    # and causes so a retry can correct the input instead of guessing.
    for key in ("errors", "issues", "validation", "source_issues", "dependencies"):
        if key in payload:
            compact[key] = _bounded_value(_diagnostic_fields(payload[key]))
    if isinstance(payload.get("handler_coverage"), dict):
        compact["handler_coverage"] = payload["handler_coverage"]
    return compact or payload


def _diagnostic_fields(value):
    """Exclude echoed arguments from structured validation feedback."""
    if isinstance(value, dict):
        return {key: _diagnostic_fields(child) for key, child in value.items()
                if key not in {"input", "input_value", "url", "ctx"}}
    if isinstance(value, list):
        return [_diagnostic_fields(child) for child in value]
    return value


def _schema_without_titles(schema):
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = {}
    for key, value in schema.items():
        if key == "title":
            continue  # A schema annotation; never a key inside a properties/default map.
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            result[key] = {name: _schema_without_titles(child) for name, child in value.items()}
        elif key in _SCHEMA_VALUES and isinstance(value, dict):
            result[key] = _schema_without_titles(value)
        elif (key in _SCHEMA_ARRAYS or key == "items") and isinstance(value, list):
            result[key] = [_schema_without_titles(child) for child in value]
        else:
            # Defaults, examples and const/enum values are data, not schemas.
            result[key] = deepcopy(value)
    return result


def model_tool_schema(schema):
    """The agent binds project, draft and current revision for tool calls."""
    result = _schema_without_titles(schema)
    properties = result.get("properties", {})
    for name in ("project_id", "draft_id", "expected_revision"):
        properties.pop(name, None)
    if isinstance(result.get("required"), list):
        result["required"] = [name for name in result["required"]
                              if name not in {"project_id", "draft_id", "expected_revision"}]
    return result


def lean_tool_schema(schema, description_limit=0):
    """Remove titles/examples; None preserves complete argument descriptions."""
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = {}
    for key, value in schema.items():
        if key == "description":
            if isinstance(value, str) and (description_limit is None or description_limit):
                result[key] = value if description_limit is None else value[:description_limit]
            continue
        if key in {"title", "examples"}:
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            result[key] = {name: lean_tool_schema(child, description_limit) for name, child in value.items()}
        elif key in _SCHEMA_VALUES and isinstance(value, dict):
            result[key] = lean_tool_schema(value, description_limit)
        elif (key in _SCHEMA_ARRAYS or key == "items") and isinstance(value, list):
            result[key] = [lean_tool_schema(child, description_limit) for child in value]
        else:
            result[key] = deepcopy(value)
    return result


def compact_project_context(payload):
    """Model view of observed state; avoid replaying verbose handoff prose."""
    if not isinstance(payload, dict) or "context_version" not in payload:
        return payload
    keep = ("revision", "endpoints", "files", "source", "source_status", "manifest_error", "binary_files")
    compact = {key: payload[key] for key in keep if key in payload}
    compact["project"] = {key: value for key, value in payload.get("project", {}).items()
                          if key == "name"}
    if isinstance(payload.get("draft"), dict):
        compact["draft"] = {key: value for key, value in payload["draft"].items()
                            if key == "name"}
    database = payload.get("database", {})
    if database.get("status") != "not_requested":
        compact["database"] = database
    if payload.get("database_setup", {}).get("status") not in {None, "missing"}:
        compact["database_setup"] = payload["database_setup"]
    if payload.get("project_notes", {}).get("content"):
        compact["project_notes"] = payload["project_notes"]
    if payload.get("environment", {}).get("names"):
        compact["environment_names"] = payload["environment"]["names"]
    compact["sdk_helpers"] = list(payload.get("helpers", {}))
    dependencies = payload.get("runtime_dependencies", {})
    compact["runtime_dependencies"] = {key: value for key, value in dependencies.items()
                                       if key in {"python", "available"}}
    return compact


def model_messages(messages, preserve_writes=False):
    """Keep the conversation, but send only the latest full source write back.

    Earlier source writes remain intact in the checkpoint. The model can read their
    current saved contents through get_project_context instead of paying to replay
    complete files on every subsequent model request. Keep the newest full write
    so a rejected call can be corrected without regenerating its source.
    """
    write_ids = [call.get("id") for message in messages if message.get("role") == "assistant"
                 for call in (message.get("tool_calls") or [])
                 if isinstance(call, dict) and isinstance(call.get("function"), dict)
                 and call["function"].get("name") == "write_draft_file"]
    newest_write_id = write_ids[-1] if write_ids else None
    tool_names = {call.get("id"): call.get("function", {}).get("name")
                  for message in messages if message.get("role") == "assistant"
                  for call in (message.get("tool_calls") or []) if isinstance(call, dict)}
    latest_context = max((index for index, message in enumerate(messages)
                          if message.get("role") == "tool"
                          and tool_names.get(message.get("tool_call_id")) == "get_project_context"), default=-1)
    latest_source = {}
    for index, message in enumerate(messages):
        if message.get("role") != "tool" or tool_names.get(message.get("tool_call_id")) != "get_project_context":
            continue
        try:
            payload = parse_json(message["content"])
        except (ValueError, TypeError, RecursionError, KeyError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("source"), dict):
            for path in payload["source"]:
                latest_source[path] = index
    result = []
    for index, message in enumerate(messages):
        projected = dict(message)
        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list):
            calls = []
            for call in message["tool_calls"]:
                if (not isinstance(call, dict) or preserve_writes or call.get("id") == newest_write_id
                        or not isinstance(call.get("function"), dict)
                        or call["function"].get("name") != "write_draft_file"):
                    calls.append(call)
                    continue
                function = call.get("function", {})
                raw = function.get("arguments")
                if not isinstance(raw, str) or len(raw.encode()) <= 2048:
                    calls.append(call)
                    continue
                try:
                    arguments = parse_json(raw)
                    if not isinstance(arguments, dict) or not isinstance(arguments.get("content"), str):
                        raise ValueError("Invalid source arguments")
                    arguments.pop("content")
                    compact = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                except (ValueError, TypeError, RecursionError):
                    calls.append(call)
                    continue
                calls.append({**call, "function": {**function, "arguments": compact}})
            projected["tool_calls"] = calls
        text = message.get("content")
        if message.get("role") == "tool" and isinstance(text, str):
            try:
                payload = parse_json(text)
                name = tool_names.get(message.get("tool_call_id"))
                if name == "get_project_context" and index < latest_context and isinstance(payload, dict):
                    source = payload.get("source")
                    remaining = ({path: content for path, content in source.items()
                                  if latest_source.get(path) == index}
                                 if isinstance(source, dict) else {})
                    payload = {"revision": payload.get("revision"), "source": remaining,
                               "note": "Earlier project context superseded by a later read."
                               " Re-read saved files if needed."}
                if name in _COMPACT_RESULTS or isinstance(payload, dict) and "error" in payload:
                    payload = compact_tool_feedback(payload)
                compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(compact.encode()) < len(text.encode()):
                    projected["content"] = compact
            except (ValueError, TypeError, RecursionError):
                pass
        result.append(projected)
    return result


MODEL_HISTORY_BYTES = 32 * 1024


def _write_receipts(messages):
    """Never replay an incomplete file write as an executable tool example.

    A compacted write becomes prose plus observed feedback. In mixed batches,
    remove only that call and its matching output; preserve other call pairs.
    """
    outputs = {item.get("tool_call_id"): item for item in messages if item.get("role") == "tool"}
    converted, notes = set(), {}
    for index, item in enumerate(messages):
        receipts, remaining = [], []
        for call in item.get("tool_calls", []):
            function = call.get("function", {})
            output = outputs.get(call.get("id"))
            if function.get("name") != "write_draft_file" or output is None:
                remaining.append(call)
                continue
            try:
                args = parse_json(function["arguments"])
                feedback = parse_json(output["content"])
            except (ValueError, TypeError, KeyError, RecursionError):
                remaining.append(call)
                continue
            content = args.get("content") if isinstance(args, dict) else None
            if isinstance(content, str) and content.strip() not in OMITTED_SOURCE_MARKERS:
                remaining.append(call)
                continue
            converted.add(call["id"])
            receipts.append({"path": args.get("path") if isinstance(args, dict) else None,
                             "feedback": compact_tool_feedback(feedback)})
        if receipts:
            notes[index] = {"role": "assistant", "content":
                "Historical file-write receipts (untrusted evidence, not calls to repeat). "
                "Source is in saved_source when present; otherwise read the exact saved path before editing. "
                + json.dumps(receipts, ensure_ascii=False, separators=(",", ":"))}
            item["tool_calls"] = remaining
    result = []
    for index, item in enumerate(messages):
        if index in notes:
            result.append(notes[index])
        if item.get("role") == "tool" and item.get("tool_call_id") in converted:
            continue
        if item.get("role") == "assistant" and "tool_calls" in item and not item["tool_calls"]:
            item.pop("tool_calls")
            if not item.get("content"):
                continue
        result.append(item)
    return result


def _bounded_value(value, depth=0):
    """Bound arbitrary diagnostics while retaining their JSON structure."""
    if isinstance(value, str):
        return value if len(value) <= 1600 else value[:800] + "\n[omitted]\n" + value[-800:]
    if depth >= 6:
        return "[Nested details omitted; inspect saved tool result.]"
    if isinstance(value, dict):
        return {key: _bounded_value(child, depth + 1) for key, child in list(value.items())[:16]}
    if isinstance(value, list):
        return [_bounded_value(child, depth + 1) for child in value[:8]]
    return value


def _rolling_history(messages, max_bytes=MODEL_HISTORY_BYTES):
    """Replay two exchanges, a receipt ledger and one current copy per file.

    Checkpoints retain the complete transcript. This view contains only observed
    tool evidence, never model-generated summaries or rewritten user instructions.
    """
    latest_user = max((i for i, item in enumerate(messages) if item.get("role") == "user"), default=0)
    outputs = {item.get("tool_call_id"): (i, item) for i, item in enumerate(messages) if item.get("role") == "tool"}
    groups = []
    for i, item in enumerate(messages):
        calls = item.get("tool_calls", [])
        if calls and all(call["id"] in outputs for call in calls):
            groups.append((i, calls, [outputs[call["id"]][0] for call in calls]))
    if len(groups) <= 2:
        return messages  # A ledger costs more than replaying a short conversation.
    keep = {i for i, _, _ in [group for group in groups if group[0] > latest_user][-2:]}
    removed, receipts, sources = set(), {}, {}
    metadata = None
    metadata_index, route_edits = -1, []
    for index, calls, results in groups:
        if index not in keep:
            removed.update([index, *results])
        for call in calls:
            name = call["function"]["name"]
            try:
                args = parse_json(call["function"]["arguments"])
                result = parse_json(outputs[call["id"]][1]["content"])
            except (ValueError, TypeError, KeyError, RecursionError):
                continue
            if not isinstance(args, dict) or not isinstance(result, dict):
                continue
            identity = {field: args.get(field) for field in ("path", "method", "endpoint")}
            if name == "upsert_endpoint" and isinstance(args.get("endpoint"), dict):
                identity["endpoint"] = {field: args["endpoint"].get(field) for field in ("method", "path")}
            key = (name, json.dumps(identity, sort_keys=True, ensure_ascii=False))
            facts = {field: result[field] for field in
                     ("revision", "status", "saved", "path", "code", "error", "tests_run", "tests_passed", "static_check", "handler_coverage")
                     if field in result}
            if args.get("path"):
                facts.setdefault("path", args["path"])
            if name == "upsert_endpoint":
                facts["endpoint"] = args.get("endpoint")
            if name == "remove_endpoint":
                facts["method"] = args.get("method")
            if ("error" in result or result.get("status") in {"failed", "error"}
                    or result.get("recovery")):
                facts.update({field: result[field] for field in
                              ("message", "output", "failures", "next_step", "errors", "issues",
                               "validation", "source_issues", "dependencies", "line", "column", "field", "loc", "recovery") if field in result})
            receipts[key] = {"tool": name, **_bounded_value(facts)}
            if index <= latest_user:
                continue
            if name in {"upsert_endpoint", "remove_endpoint"} and "error" not in result:
                route_edits.append((index, {"tool": name, **facts}))
            if name == "get_project_context":
                if "files" in result:
                    metadata_index = index
                    metadata = {field: result[field] for field in
                                ("project", "draft", "files", "endpoints", "manifest_error", "environment_names", "runtime_dependencies", "sdk_helpers", "project_notes", "database", "database_setup")
                                if field in result}
                for path, source in result.get("source", {}).items():
                    sources[path] = source
                if index in keep:
                    outputs[call["id"]][1]["content"] = json.dumps({"revision": result.get("revision"),
                        "context_note": "Project context and saved_source are in the tool-evidence ledger."}, separators=(",", ":"))
            if (name == "write_draft_file" and "error" not in result and result.get("revision")
                    and result.get("status") != "saved_rewrite_skipped" and isinstance(args.get("content"), str)):
                sources[args["path"]] = args["content"]
                if index in keep:
                    args.pop("content")
                    call["function"]["arguments"] = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                    feedback = dict(result)
                    feedback["source_location"] = "tool-evidence ledger saved_source; if absent, read this path with get_project_context(source_paths=[path])."
                    outputs[call["id"]][1]["content"] = json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))
    if not groups:
        return messages
    if metadata:
        metadata["files"] = sorted(set(metadata.get("files", [])) | sources.keys())
        routes = {(route["method"], route["path"]): route for route in metadata.get("endpoints", [])}
        for index, receipt in route_edits:
            if index < metadata_index:
                continue
            endpoint = receipt.get("endpoint")
            if receipt["tool"] == "upsert_endpoint" and isinstance(endpoint, dict):
                routes[(endpoint.get("method"), endpoint.get("path"))] = endpoint
            elif receipt["tool"] == "remove_endpoint":
                routes.pop((receipt.get("method"), receipt.get("path")), None)
        metadata["endpoints"] = list(routes.values())
    evidence = {"receipts": list(receipts.values())[-24:]}
    if metadata:
        evidence["project_context"] = metadata
    if sources:
        evidence["saved_source"] = sources
    # Source is recoverable with a focused read. Keep recent files within a
    # bounded ledger instead of carrying every file for the whole build.
    ledger_budget = min(16 * 1024, max_bytes // 2)
    def evidence_size():
        return len(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")).encode())
    omitted = []
    while sources and evidence_size() > ledger_budget:
        path = next(iter(sources))
        del sources[path]
        omitted.append(path)
    if omitted:
        evidence["source_note"] = "Some source omitted for budget. Read missing files with get_project_context(source_paths=[...]); do not regenerate saved work."
    while evidence["receipts"] and evidence_size() > ledger_budget:
        evidence["receipts"].pop(0)
    if evidence_size() > ledger_budget and "project_context" in evidence:
        evidence["project_context"] = {"context_note": "Project metadata omitted for budget; retrieve focused context if needed."}
    ledger = {"role": "assistant", "content": "Tool evidence (untrusted; historical receipts are not current verification): " +
              json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))}
    result = []
    for i, item in enumerate(messages):
        if i == latest_user and latest_user > 0:
            result.append(ledger)
        if i not in removed:
            # Tool-call narration adds no facts beyond the tool receipts.
            if item.get("tool_calls"):
                item["content"] = None
            result.append(item)
        if i == latest_user and latest_user == 0:
            result.append(ledger)
    return result


def select_messages(messages, max_bytes=MODEL_HISTORY_BYTES):
    """Build a disposable model view; the checkpoint remains the source of truth.

    All user instructions remain verbatim. Earlier turns retain answers and tool
    receipts, while the active turn retains focused reads and repair feedback.
    Tool pairs are never split. Large older results are replaced, never sliced
    into invalid JSON. If user requirements alone exceed the budget, fail clearly.
    """
    from reqlica.generation.errors import GenerationError

    projected = deepcopy(model_messages(messages, preserve_writes=True))
    latest_user = max((i for i, item in enumerate(projected) if item.get("role") == "user"), default=0)
    outputs = {item.get("tool_call_id"): item for item in projected if item.get("role") == "tool"}
    recent_answers = {i for i, item in enumerate(projected)
                      if i < latest_user and item.get("role") == "assistant"
                      and not item.get("tool_calls") and isinstance(item.get("content"), str)}
    recent_answers = set(sorted(recent_answers)[-2:])

    def receipt(item):
        try:
            payload = parse_json(item.get("content", ""))
        except (ValueError, TypeError, RecursionError):
            payload = {"message": item.get("content", "")}
        if not isinstance(payload, dict):
            payload = {"result": payload}
        facts = compact_tool_feedback(payload)
        facts = {key: value for key, value in facts.items() if key not in {"source", "manifest", "endpoints"}}
        facts["context_note"] = "Historical result; inspect current saved state with focused tools before relying on it."
        item["content"] = json.dumps(_bounded_value(facts), ensure_ascii=False, separators=(",", ":"))

    saved_writes = {}
    tool_names = {}
    for i, item in enumerate(projected):
        for call in item.get("tool_calls", []):
            tool_names[call["id"]] = call["function"]["name"]
            if call["function"]["name"] == "write_draft_file":
                try:
                    args = parse_json(call["function"]["arguments"])
                    feedback = parse_json(outputs.get(call["id"], {}).get("content", "{}"))
                    if (isinstance(feedback, dict) and "error" not in feedback and feedback.get("revision")
                            and feedback.get("status") != "saved_rewrite_skipped"):
                        saved_writes[args["path"]] = i
                except (ValueError, KeyError, TypeError, RecursionError):
                    pass
    for i, item in enumerate(projected):
        if item.get("role") == "tool" and tool_names.get(item.get("tool_call_id")) == "get_project_context":
            try:
                payload = parse_json(item["content"])
                payload = compact_project_context(payload)
                if isinstance(payload, dict) and isinstance(payload.get("source"), dict):
                    stale = [path for path in payload["source"] if saved_writes.get(path, -1) > i]
                    for path in stale:
                        del payload["source"][path]
                    if stale:
                        payload["context_note"] = "Source changed after this read; re-read saved files if needed."
                item["content"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            except (ValueError, KeyError, TypeError, RecursionError):
                pass
        for call in item.get("tool_calls", []):
            function = call["function"]
            if function.get("name") == "write_draft_file":
                try:
                    args = parse_json(function["arguments"])
                    output = outputs.get(call["id"], {})
                    feedback = parse_json(output.get("content", "{}"))
                    rejected = isinstance(feedback, dict) and "error" in feedback
                    # Keep the latest saved implementation for each active file.
                    # Removing every successful write made the model regenerate it
                    # instead of registering its route and finishing the task.
                    if (isinstance(args.get("content"), str) and args["content"].strip() in OMITTED_SOURCE_MARKERS):
                        # Old checkpoints may contain a model's copy of a former
                        # compaction marker. Do not teach it to copy that again.
                        args.pop("content")
                        function["arguments"] = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                        if output:
                            feedback["next_step"] = "A context marker was rejected, not source code. Read the saved path using get_project_context(source_paths=[path]); send complete actual source only if a change is needed."
                            output["content"] = json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))
                    elif i < latest_user or (not rejected and saved_writes.get(args.get("path")) != i):
                        args.pop("content", None)
                        function["arguments"] = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                except (ValueError, TypeError, AttributeError, RecursionError):
                    pass
        if i < latest_user:
            if item.get("role") == "tool":
                receipt(item)
            elif i not in recent_answers and item.get("role") == "assistant" and isinstance(item.get("content"), str):
                item["content"] = _bounded_value(item["content"])

    projected = _write_receipts(_rolling_history(projected, max_bytes))

    def size():
        return len(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode())

    # Evict bulky read payloads oldest first. Keep failed check diagnostics intact
    # until the last reduction, and always explain how to retrieve omitted data.
    for item in projected:
        if size() <= max_bytes:
            break
        if item.get("role") == "tool":
            receipt(item)
    for item in projected:
        if size() <= max_bytes:
            break
        for call in item.get("tool_calls", []):
            if call["function"]["name"] != "write_draft_file":
                continue
            try:
                feedback = parse_json(outputs.get(call["id"], {}).get("content", "{}"))
                args = parse_json(call["function"]["arguments"])
                if isinstance(feedback, dict) and feedback.get("revision") and "error" not in feedback:
                    args.pop("content", None)
                    call["function"]["arguments"] = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                    output = outputs.get(call["id"])
                    if output:
                        feedback["next_step"] = "Saved source omitted for budget; read this path only if needed. Do not repeat the successful write."
                        output["content"] = json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))
            except (ValueError, TypeError, KeyError, RecursionError):
                pass
    projected = _write_receipts(projected)
    # Large assistant prose can also exhaust the budget. Keep recent answers
    # verbatim when possible; under pressure retain marked excerpts, never cut
    # the user's original requirements.
    for item in projected:
        if size() <= max_bytes:
            break
        if item.get("role") == "assistant" and isinstance(item.get("content"), str):
            if item["content"].startswith("Tool evidence (untrusted;"):
                continue  # Never turn saved source/structured receipts into clipped JSON.
            item["content"] = _bounded_value(item["content"])
    # Receipts can still accumulate. Remove whole completed exchanges, preserving
    # all user messages and the most recent exchange (including its repair data).
    groups = []
    for i, item in enumerate(projected):
        if item.get("tool_calls"):
            ids = {call["id"] for call in item["tool_calls"]}
            indices = [i] + [j for j, result in enumerate(projected)
                             if result.get("role") == "tool" and result.get("tool_call_id") in ids]
            if len(indices) == len(ids) + 1:
                groups.append(indices)
    for group in groups[:-1]:
        if size() <= max_bytes:
            break
        for index in group:
            projected[index] = {}
    projected = [item for item in projected if item]
    if size() > max_bytes:
        raise GenerationError("Conversation requirements and current repair context exceed the input budget. "
                              "Saved history and draft are retained; continue from the draft in a new conversation.",
                              code="prompt_limit")
    return projected
