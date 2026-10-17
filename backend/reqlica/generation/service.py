"""Generate a fresh draft from published context; never execute model output."""

import ast
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import SecretStr, ValidationError
from reqlica.generation import provider
from reqlica.generation.errors import GenerationError
from reqlica.generation.prompts import build_messages
from reqlica.generation.schemas import GeneratedPlan, GenerationRequest
from reqlica.projects import drafts
from reqlica.projects.database_schema import read_database_schema
from reqlica.projects.store import Manifest, get_project
from reqlica.projects.versions import code_directory, content_digest, project_lock


@dataclass
class _ObjectPairs:
    """Keep object members distinct from arrays, without JSON's last-value wins."""

    pairs: list[tuple[str, object]]


def _strict_value(value):
    if isinstance(value, _ObjectPairs):
        result = {}
        for key, item in value.pairs:
            if key in result:
                raise ValueError(
                    "The proposal contains duplicate JSON keys in a nested object"
                )
            result[key] = _strict_value(item)
        return result
    if isinstance(value, list):
        return [_strict_value(item) for item in value]
    return value


def _parse_proposal(text: str) -> tuple[object, list[str]]:
    root = json.loads(text, object_pairs_hook=_ObjectPairs)
    if not isinstance(root, _ObjectPairs):
        return root, []  # The plan schema rejects non-object roots.
    result, counts = {}, {}
    for key, raw in root.pairs:
        value = _strict_value(raw)
        if key in result:
            # Groq sometimes emits separate handler/test file arrays under the
            # same key. Concatenation is lossless, unlike normal json.loads().
            # Never choose between duplicate scalars, nested fields, or edits.
            if key not in {"files", "endpoints"}:
                raise ValueError(
                    "The proposal contains ambiguous duplicate JSON metadata keys"
                )
            if not isinstance(result[key], list) or not isinstance(value, list):
                raise ValueError(
                    "Repeated files/endpoints JSON keys must contain arrays"
                )
            result[key].extend(value)
        else:
            result[key] = value
        counts[key] = counts.get(key, 0) + 1
    warnings = [
        f"Combined {count} top-level {key} arrays without discarding items; static output checks passed (code not executed)."
        for key, count in counts.items()
        if count > 1
    ]
    return result, warnings


def _validate_plan(text: str, context: dict) -> tuple[GeneratedPlan, list[str]]:
    try:
        payload, warnings = _parse_proposal(text)
        plan = GeneratedPlan.model_validate(payload)
        if not plan.summary.strip() or len(plan.summary) > 8192:
            raise ValueError("A concise generation summary is required")
        if len(plan.assumptions) > 50 or any(len(a) > 4096 for a in plan.assumptions):
            raise ValueError("Too many or oversized assumptions")
        if not plan.files or not plan.endpoints:
            raise ValueError(
                "No implementation returned; requirements may need clarification or unsupported schema changes"
            )
        if len(plan.files) > 20 or len(plan.endpoints) > 50:
            raise ValueError(
                "The initial generator accepts at most 20 files and 50 endpoints"
            )
        source = {p: s for p, s in context["source"].items() if p.endswith(".py")}
        names = {p.casefold(): p for p in context["files"]}
        generated = set()
        for file in plan.files:
            path = PurePosixPath(file.path)
            if (
                path.is_absolute()
                or path.as_posix() != file.path
                or "\\" in file.path
                or len(path.parts) < 2
                or path.parts[0] not in {"endpoints", "models", "tests"}
                or path.suffix != ".py"
                or not path.stem.isidentifier()
                or any(not part.isidentifier() for part in path.parts[:-1])
                or any(
                    part in {"__pycache__", "versions", "drafts"} for part in path.parts
                )
            ):
                raise ValueError(
                    "Only canonical Python paths under endpoints/, models/, tests/ are allowed"
                )
            folded = file.path.casefold()
            if folded in generated or (folded in names and names[folded] != file.path):
                raise ValueError("Duplicate or case-colliding generated file path")
            if len(file.content.encode("utf-8")) > 64 * 1024:
                raise ValueError("Generated files must each fit within 64 KiB")
            generated.add(folded)
            source[file.path] = file.content
        trees = {path: ast.parse(code, filename=path) for path, code in source.items()}
        new_tests = [
            file.path
            for file in plan.files
            if file.path.startswith("tests/test_")
            and any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
                for node in ast.walk(trees[file.path])
            )
        ]
        if not new_tests:
            raise ValueError(
                "The proposal must include a tests/test_*.py module with tests"
            )
        # Reject duplicate/equivalent proposal routes.
        Manifest(endpoints=plan.endpoints)
        routes = {(e["method"], e["path"]): e for e in context["endpoints"]}
        routes.update({(e.method, e.path): e.model_dump() for e in plan.endpoints})
        merged = Manifest(endpoints=list(routes.values()))
        for endpoint in merged.endpoints:
            filename, function = endpoint.handler.split(":")
            tree = trees.get(filename)
            if tree is None or not any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function
                for node in tree.body
            ):
                raise ValueError(
                    "A resulting route references a missing top-level handler"
                )
        return plan, warnings
    except (ValueError, SyntaxError, RecursionError) as error:
        # Never echo a raw provider response/source in a public error.
        if isinstance(error, ValidationError):
            detail = "Invalid JSON/schema"
        elif isinstance(error, SyntaxError):
            detail = "Generated Python has invalid syntax"
        else:
            detail = str(error)
        raise GenerationError("Invalid generated proposal: " + detail[:500]) from error


def generate_endpoints(
    project_id: str, request: GenerationRequest, api_key: SecretStr | None = None
) -> dict:
    context, messages = build_messages(project_id, request)
    text, usage = provider.complete(messages, request, api_key)
    plan, output_warnings = _validate_plan(text, context)
    project = get_project(project_id)
    with project_lock(project.directory):
        current = get_project(project_id)
        if (
            str(current.directory) != context["project"]["directory"]
            or current.active_version != context["project"]["active_version"]
            or content_digest(code_directory(current)) != context["revision"]
        ):
            raise ValueError(
                "Published project changed during generation; no draft was saved"
            )
        fingerprint = context["database"].get("fingerprint")
        if (
            fingerprint
            and read_database_schema(current).get("fingerprint") != fingerprint
        ):
            raise ValueError(
                "Database definitions changed during generation; request fresh context"
            )
        # No provider output was imported. All response/path/AST/manifest checks
        # happen before creating a draft, and no project lock spans the network.
        state = drafts.create_draft(project_id)
        try:
            for file in plan.files:
                state = drafts.write_draft_file(
                    project_id, state["id"], file.path, file.content, state["revision"]
                )
            for endpoint in plan.endpoints:
                state = drafts.upsert_endpoint(
                    project_id, state["id"], endpoint, state["revision"]
                )
        except (OSError, ValueError) as error:
            # Preserve the new draft for inspection/recovery instead of hiding a
            # partially completed disk write or touching somebody else's draft.
            raise GenerationError(
                "Could not finish saving the generated draft; inspect it before retrying",
                500,
                draft_id=state["id"],
            ) from error
    return {
        "status": "draft_created",
        "draft": state,
        "summary": plan.summary,
        "assumptions": plan.assumptions,
        "output_warnings": output_warnings,
        "generated_files": [file.path for file in plan.files],
        "endpoints": [endpoint.model_dump() for endpoint in plan.endpoints],
        "provider": {"kind": "groq", "model": request.model, "usage": usage},
        "context_revision": context["revision"],
        "checks": "not_run",
        "warning": "Generated Python is untrusted and not sandboxed. Review it before running validation/tests. Approval and activation remain explicit.",
    }
