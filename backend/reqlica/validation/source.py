"""Non-executing name checks shared by generation, repair, and validation."""

import ast

from pyflakes.checker import Checker
from pyflakes.messages import UndefinedLocal, UndefinedName


def name_issues(
    trees: dict[str, ast.Module], *, paths: set[str] | None = None
) -> list[dict]:
    issues = []
    for path, tree in sorted(trees.items()):
        if paths is not None and path not in paths:
            continue
        # ast.parse alone accepts invalid execution contexts such as a module-level
        # return or await. Compilation checks these without executing user code.
        compile(tree, path, "exec", dont_inherit=True)
        messages = sorted(Checker(tree, filename=path).messages, key=lambda m: m.lineno)
        for message in messages:
            # Style, unused imports, and star-import ambiguity are not failures.
            if isinstance(message, (UndefinedName, UndefinedLocal)):
                issues.append(
                    {
                        "code": "undefined_name",
                        "path": path,
                        "line": message.lineno,
                        "message": message.message % message.message_args,
                    }
                )
                if len(issues) >= 20:
                    return issues
        issues.extend(serialization_issues(tree, path))
    return issues[:20]


def name_issue_summary(issues: list[dict]) -> str:
    return "\n".join(f"{i['path']}:{i['line']}: {i['message']}" for i in issues)


def serialization_issues(tree: ast.Module, path: str) -> list[dict]:
    """Catch a definite unsafe encoding pattern without guessing model field types."""
    responses = set()
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "fastapi.responses", "starlette.responses"
        }:
            responses.update(alias.asname or alias.name for alias in node.names
                             if alias.name == "JSONResponse")
        elif isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names
                           if alias.name in {"fastapi.responses", "starlette.responses"})
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = ast.unparse(node.func)
        if function not in responses and function not in {m + ".JSONResponse" for m in modules}:
            continue
        content = next((k.value for k in node.keywords if k.arg == "content"),
                       node.args[0] if node.args else None)
        # Do not flag explicitly encoded content; inspect only bare model dumps,
        # including dumps nested in literal response objects/arrays.
        def unsafe(value):
            if isinstance(value, ast.Dict):
                return any(unsafe(v) for v in value.values)
            if isinstance(value, (ast.List, ast.Tuple)):
                return any(unsafe(v) for v in value.elts)
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
                return False
            if value.func.attr == "model_dump_json":
                return True
            if value.func.attr != "model_dump":
                return False
            return not any(k.arg == "mode" and isinstance(k.value, ast.Constant)
                           and k.value.value == "json" for k in value.keywords)
        if unsafe(content):
            issues.append({
                "code": "unsafe_json_response", "path": path, "line": node.lineno,
                "message": "Raw JSONResponse with model_dump() can fail on datetime/UUID; "
                           "use APIJSONResponse(content=model), jsonable_encoder(content), "
                           "or model_dump(mode='json'). Do not double-encode model_dump_json().",
            })
    return issues
