"""Shared import policy and diagnostics. Inspect metadata/AST, never import handlers.

This is a predictability check, not a sandbox or an automatic package installer.
Only unconditional, declared Reqlica dependencies and the SDK are offered to AI.
"""

import ast
import importlib.util
import re
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import PurePosixPath

DEPENDENCY_PROMPT = """Use runtime_dependencies as the shared dependency policy.
Only Python standard-library modules available on this platform, imports listed in
runtime_dependencies.available, local project modules, and reqlica.runtime.sdk
are supported for generated code. Packages installed but not declared are NOT
approved. Do not invent dependencies, install packages, run pip/uv, or write package
manifests. Clarification must not offer unavailable libraries as working choices.
If the user explicitly requires an unavailable library, explain the dependency
blocker and ask them to review/install it outside generation, or choose a supported
alternative. Never silently replace an explicitly requested algorithm/library.
For password hashing, hashlib.scrypt or hashlib.pbkdf2_hmac with a random per-user
salt, appropriate work factor and constant-time verification are stdlib options;
plain SHA/MD5 is not password hashing. For JWT, use an available approved library
or standards-compliant HS256 via hmac/hashlib/base64/json, validating algorithm,
signature and expiration with a stable configured key. Never use fake tokens.
If choosing bcrypt, account for its 72-byte password limit; never silently truncate
passwords. Clarify conflicts with explicit password limits (including UTF-8 byte
lengths), or use a supported scheme compatible with the specified contract.
"""

# Import names do not always match package names. Never construct install commands
# from an arbitrary model-provided import (e.g. jwt is distributed as PyJWT).
KNOWN_PACKAGES = {
    "bcrypt": "bcrypt",
    "jwt": "PyJWT",
    "argon2": "argon2-cffi",
    "cryptography": "cryptography",
    "email_validator": "email-validator",
}
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def _available(root: str) -> bool:
    # Probe top-level specs only: find_spec('a.b') could execute a/__init__.py.
    try:
        return importlib.util.find_spec(root) is not None
    except (ImportError, ValueError, AttributeError):
        return False


@lru_cache(maxsize=1)
def _declared_modules() -> tuple[tuple[str, str], ...]:
    try:
        requirements = metadata.requires("reqlica") or []
    except metadata.PackageNotFoundError:
        requirements = []
    names = set()
    for requirement in requirements:
        # Extras/conditional dependencies aren't automatically application APIs.
        if ";" not in requirement and (
            match := re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        ):
            names.add(re.sub(r"[-_.]+", "-", match[0]).lower())
    modules = {"fastapi": "fastapi", "pydantic": "pydantic", "httpx": "httpx"}
    modules.update(
        {
            root: package
            for root, package in KNOWN_PACKAGES.items()
            if re.sub(r"[-_.]+", "-", package).lower() in names
        }
    )
    for root, packages in metadata.packages_distributions().items():
        if _MODULE.fullmatch(root) and "." not in root:
            for package in packages:
                if re.sub(r"[-_.]+", "-", package).lower() in names:
                    modules[root] = package
    return tuple(sorted(modules.items()))


def runtime_dependency_policy() -> dict:
    modules = dict(_declared_modules())
    available = {root: package for root, package in modules.items() if _available(root)}
    versions = {}
    for package in sorted(set(available.values())):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            pass
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "available": available,
        "package_versions": versions,
        "missing_declared": {
            root: package for root, package in modules.items() if root not in available
        },
        "sdk": "reqlica.runtime.sdk",
        "installation": "Manual, reviewed changes to Reqlica dependencies only; restart and rerun checks. No automatic installation.",
    }


def dependency_message(
    module: str | None, *, code: str = "missing_dependency", package: str | None = None
) -> str:
    module = (
        module
        if module and len(module) <= 200 and _MODULE.fullmatch(module)
        else "unknown_module"
    )
    root = module.split(".")[0]
    if root in {"endpoints", "models", "tests"}:
        code = "missing_local_module"
    if code == "missing_local_module":
        return f"Missing project module: {module}. Include the local module or correct its import; do not install a similarly named package."
    if root == "reqlica" and code == "unsupported_dependency":
        return "Unsupported Reqlica import: generated handlers may use only reqlica.runtime.sdk, not Reqlica's internal management modules."
    if code == "missing_dependency" and "." in module and _available(root):
        return f"Missing Python submodule: {module}. The top-level module is available; check the import spelling, installed package version, or installation integrity. No package was installed automatically."
    if root in sys.stdlib_module_names:
        return f"Python standard-library module unavailable on this platform: {module}. Use a portable alternative; this is not a package to install from PyPI."
    package = package or KNOWN_PACKAGES.get(root)
    label = (
        "Unsupported generated import"
        if code == "unsupported_dependency"
        else "Missing Python dependency"
    )
    message = f"{label}: {module}. "
    if package and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", package):
        message += f"After reviewing the package, run `uv add {package}` in Reqlica's directory, then restart Reqlica and rerun Validate and Run tests. This changes the shared runtime. "
    else:
        message += "Check local module spelling first. Otherwise use a supported library, or review the correct distribution name and explicitly add it to Reqlica's dependencies, then restart and rerun checks. "
    return message + "No package was installed automatically."


def import_issues(
    trees: dict[str, ast.Module],
    *,
    paths: set[str] | None = None,
    approved_only: bool = False,
    policy: dict | None = None,
) -> list[dict]:
    """Check static import targets without executing them; symbols aren't resolved.

    Manual drafts keep working with installed libraries outside the AI policy.
    Optional/type-only imports are skipped there, but not in generated proposals.
    Computed imports and attributes still require explicit runtime checks.
    """
    policy = policy if policy is not None else runtime_dependency_policy()
    local = set()
    for filename in trees:
        parts = list(PurePosixPath(filename).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        local.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    roots = {module.split(".")[0] for module in local}
    issues = []

    for filename, tree in sorted(trees.items()):
        if paths is not None and filename not in paths:
            continue
        optional = set()
        if not approved_only:
            for node in ast.walk(tree):
                if isinstance(node, (ast.Try, ast.If)):
                    type_check = isinstance(node, ast.If) and (
                        isinstance(node.test, ast.Name)
                        and node.test.id == "TYPE_CHECKING"
                        or isinstance(node.test, ast.Attribute)
                        and node.test.attr == "TYPE_CHECKING"
                    )
                    catches_import = isinstance(node, ast.Try) and any(
                        handler.type is None
                        or any(
                            isinstance(n, ast.Name)
                            and n.id in {"ImportError", "ModuleNotFoundError"}
                            for n in ast.walk(handler.type)
                        )
                        for handler in node.handlers
                    )
                    if type_check or catches_import:
                        optional.update(
                            id(child)
                            for statement in node.body
                            for child in ast.walk(statement)
                        )
        for node in ast.walk(tree):
            if id(node) in optional:
                continue
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    parent = list(PurePosixPath(filename).parent.parts)
                    if node.level > len(parent):
                        targets = ["invalid_relative_import"]
                    else:
                        targets = [
                            ".".join(
                                parent[: len(parent) - node.level + 1]
                                + ([target] if target else [])
                            )
                        ]
                else:
                    targets = [target]
            for target in targets:
                root = target.split(".")[0]
                code = None
                if root in roots or root in {"endpoints", "models", "tests"}:
                    if target not in local:
                        code = "missing_local_module"
                elif target == "invalid_relative_import":
                    code = "missing_local_module"
                elif approved_only and root == "reqlica":
                    if (
                        target not in {"reqlica.runtime.sdk", "reqlica.runtime"}
                        or target == "reqlica.runtime"
                        and any(alias.name != "sdk" for alias in node.names)
                    ):
                        code = "unsupported_dependency"
                elif (
                    approved_only
                    and root not in sys.stdlib_module_names
                    and root not in policy["available"]
                ):
                    code = "unsupported_dependency"
                elif not _available(root):
                    code = "missing_dependency"
                if code:
                    package = policy["missing_declared"].get(root) or policy[
                        "available"
                    ].get(root)
                    issues.append(
                        {
                            "code": code,
                            "path": filename,
                            "line": node.lineno,
                            "module": target,
                            "message": dependency_message(
                                target, code=code, package=package
                            ),
                        }
                    )
                    if len(issues) >= 20:
                        return issues
    return issues


def import_issue_summary(issues: list[dict]) -> str:
    first = issues[0]
    return f"{first['path']}:{first['line']}: {first['message']}" + (
        f" ({len(issues)} import issues reported; at most 20 shown.)"
        if len(issues) > 1
        else ""
    )
