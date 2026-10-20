"""Structural coverage guards for AI corrections to existing test modules.

These checks catch common coverage loss; they cannot prove semantic equivalence.
The original contract and runtime checks remain independent.
"""

import ast
from collections import Counter


def _dump(node):
    return ast.dump(node, include_attributes=False)


def _tests(tree):
    result = {}

    def visit(node, prefix=()):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                key = (*prefix, child.name)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test"):
                    if key in result:
                        raise ValueError("Duplicate test identities are not allowed")
                    result[key] = child
                visit(child, key)
    visit(tree)
    return result


def _assertions(node):
    counts = Counter()
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            counts["assert"] += 1
        elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr.startswith("assert") or child.func.attr == "fail":
                counts[child.func.attr] += 1
    return counts


def _status_assertions(node):
    result = Counter()
    for child in ast.walk(node):
        is_assertion = isinstance(child, ast.Assert) or (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr.startswith("assert")
        )
        if is_assertion and any(
            isinstance(part, ast.Attribute) and part.attr == "status_code"
            for part in ast.walk(child)
        ):
            result[_dump(child)] += 1
    return result


def validate_test_correction(before: ast.Module, after: ast.Module) -> None:
    """Permit fixture/assertion API corrections while retaining test structure."""
    original, proposed = _tests(before), _tests(after)
    if not original or not original.keys() <= proposed.keys():
        raise ValueError("Test correction cannot remove or rename existing tests")
    for name, old in original.items():
        new = proposed[name]
        if type(old) is not type(new) or _dump(old.args) != _dump(new.args):
            raise ValueError("Test correction cannot change test signatures")
        if [_dump(d) for d in old.decorator_list] != [_dump(d) for d in new.decorator_list]:
            raise ValueError("Test correction cannot change test decorators")
        if _assertions(old) - _assertions(new):
            raise ValueError("Test correction cannot remove assertions")
        if _status_assertions(old) - _status_assertions(new):
            raise ValueError("Test correction cannot change HTTP status assertions")
        # Added early exits and exception handlers can conceal assertion failures.
        for kind in (ast.Return, ast.Try, ast.TryStar):
            old_nodes = Counter(_dump(n) for n in ast.walk(old) if isinstance(n, kind))
            new_nodes = Counter(_dump(n) for n in ast.walk(new) if isinstance(n, kind))
            if new_nodes - old_nodes:
                raise ValueError("Test correction cannot add early exits or exception handlers")
    old_classes = {n.name: n for n in ast.walk(before) if isinstance(n, ast.ClassDef)}
    new_classes = {n.name: n for n in ast.walk(after) if isinstance(n, ast.ClassDef)}
    for name, old in old_classes.items():
        new = new_classes.get(name)
        if new is None or (
            [_dump(n) for n in old.bases + old.decorator_list + old.keywords]
            != [_dump(n) for n in new.bases + new.decorator_list + new.keywords]
        ):
            raise ValueError("Test correction must preserve test classes and decorators")

    def disabling_calls(tree):
        return Counter(
            _dump(n) for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (
                isinstance(n.func, ast.Attribute)
                and n.func.attr in {"SkipTest", "skip", "skipIf", "skipUnless", "skipTest", "expectedFailure"}
                or isinstance(n.func, ast.Name)
                and n.func.id in {"SkipTest", "skip", "skipIf", "skipUnless", "expectedFailure"}
            )
        )
    if disabling_calls(after) - disabling_calls(before):
        raise ValueError("Test correction cannot add skipped or expected-failure tests")
    if _assertions(before) - _assertions(after):
        raise ValueError("Test correction cannot remove shared helper assertions")
    if _status_assertions(before) - _status_assertions(after):
        raise ValueError("Test correction cannot change shared HTTP status assertions")
