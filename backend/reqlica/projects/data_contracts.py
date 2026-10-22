"""Read application field contracts without importing project Python or reading rows."""

import ast
import json
import re
from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from reqlica.generation.errors import GenerationError

from reqlica.projects.schemas import get_api_schema
from reqlica.projects.store import get_project
from reqlica.projects.versions import (
    _code_entries, _read_code_file, code_directory, content_digest, project_lock,
)

MAX_SOURCE_BYTES = 512 * 1024
FIELD_LIMITS = {
    "min_length": "minLength", "max_length": "maxLength", "pattern": "pattern",
    "ge": "minimum", "gt": "exclusiveMinimum", "le": "maximum",
    "lt": "exclusiveMaximum", "multiple_of": "multipleOf",
}


class DataContractError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Records do not match application field formats or constraints")


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value) + "." + node.attr
    return ""


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _annotation(node, aliases):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _annotation(ast.parse(node.value, mode="eval").body, aliases)
        except SyntaxError:
            return {}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        choices = [_annotation(node.left, aliases), _annotation(node.right, aliases)]
        return {"anyOf": choices} if all(choices) else {}
    if isinstance(node, ast.Constant) and node.value is None:
        return {"type": "null"}
    if isinstance(node, ast.Subscript):
        kind = aliases.get(_name(node.value), _name(node.value)).rsplit(".", 1)[-1]
        parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if kind == "Annotated":
            result = _annotation(parts[0], aliases)
            for metadata in parts[1:]:
                result.update(_field(metadata))
            return result
        if kind in {"Optional", "Union"}:
            choices = [_annotation(part, aliases) for part in parts]
            if kind == "Optional":
                choices.append({"type": "null"})
            return {"anyOf": choices} if all(choices) else {}
        if kind == "Literal":
            return {"enum": [_literal(part) for part in parts]}
        return {}
    kind = aliases.get(_name(node), _name(node)).rsplit(".", 1)[-1]
    if kind in {"UUID", "UUID1", "UUID3", "UUID4", "UUID5", "UUID6", "UUID7", "UUID8"}:
        return {"type": "string", "format": kind.lower()}
    formats = {"datetime": "date-time", "AwareDatetime": "date-time", "date": "date", "time": "time", "EmailStr": "email", "IPv4Address": "ipv4", "IPv6Address": "ipv6"}
    if kind in formats:
        return {"type": "string", "format": formats[kind]}
    types = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "None": "null"}
    return {"type": types[kind]} if kind in types else {}


def _field(node):
    if not isinstance(node, ast.Call) or _name(node.func).rsplit(".", 1)[-1] != "Field":
        return {}
    return {FIELD_LIMITS[key.arg]: _literal(key.value) for key in node.keywords
            if key.arg in FIELD_LIMITS and _literal(key.value) is not None}


def _model_tables(name, table_names):
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|[0-9]+", name)
    decoration = {"create", "update", "read", "write", "get", "list", "patch", "delete", "request", "response", "out", "in", "input", "output", "public", "dto", "model", "schema"}
    identity = "".join(word.lower() for word in words if word.lower() not in decoration)
    for table in table_names:
        stem = table.lower().replace("_", "")
        candidates = {stem, stem[:-1] if stem.endswith("s") else stem,
                      stem[:-2] if stem.endswith("es") else stem,
                      stem[:-3] + "y" if stem.endswith("ies") else stem}
        if identity in candidates:
            yield table


def application_data_contract(project_id, database_schema):
    project = get_project(project_id)
    with project_lock(project.directory):
        directory = code_directory(project)
        revision = content_digest(directory)
        sources, total = {}, 0
        for relative, is_directory in _code_entries(directory):
            if is_directory or relative.suffix != ".py" or any(part.startswith(".") or part in {"tests", "test"} for part in relative.parts) or relative.name.startswith("test_"):
                continue
            raw = _read_code_file(directory, relative)
            total += len(raw)
            if total > MAX_SOURCE_BYTES:
                raise ValueError("Application source exceeds the mock-data contract limit")
            sources[relative.as_posix()] = raw.decode("utf-8")
        try:
            api_models = get_api_schema(project_id)["schema"].get("components", {}).get("schemas", {})
        except (OSError, ValueError):
            api_models = {}
        table_columns = {table["name"]: {c["name"] for c in table["columns"]} for table in database_schema["tables"]}
        models, trees, model_problems = {}, [], {}
        for path, text in sources.items():
            try:
                tree = ast.parse(text)
            except SyntaxError as error:
                raise ValueError("Published application source is invalid; repair it before generating data") from error
            aliases = {}
            for item in ast.walk(tree):
                if isinstance(item, ast.ImportFrom):
                    for alias in item.names:
                        aliases[alias.asname or alias.name] = (item.module or "") + "." + alias.name
                elif isinstance(item, ast.Import):
                    for alias in item.names:
                        aliases[alias.asname or alias.name] = alias.name
            trees.append((path, tree, aliases))
            for item in ast.walk(tree):
                if not isinstance(item, ast.ClassDef):
                    continue
                bases = [aliases.get(_name(base), _name(base)).rsplit(".", 1)[-1] for base in item.bases]
                if item.name not in api_models and not any(base in {"BaseModel", "RootModel"} or base in models for base in bases):
                    if bases:
                        model_problems[item.name] = ["an unresolved application model type"]
                    continue
                problems = model_problems.setdefault(item.name, [])
                for base in bases:
                    problems.extend(model_problems.get(base, []))
                    if base not in {"BaseModel", "RootModel"} and base not in models:
                        problems.append("custom or unresolved model inheritance")
                for member in item.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for decorator in member.decorator_list:
                            target = decorator.func if isinstance(decorator, ast.Call) else decorator
                            kind = aliases.get(_name(target), _name(target)).rsplit(".", 1)[-1]
                            if kind in {"field_validator", "model_validator", "validator", "root_validator", "field_serializer", "model_serializer"}:
                                problems.append("custom Python validation or serialization")
                    if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name) and member.target.id != "model_config":
                        if not _annotation(member.annotation, aliases) and member.target.id not in api_models.get(item.name, {}).get("properties", {}):
                            problems.append("an unsupported field type: " + member.target.id)
                        if any(isinstance(node, ast.Call) and _name(node.func).rsplit(".", 1)[-1] != "Field" for node in ast.walk(member.annotation)):
                            problems.append("custom annotation logic")
                properties = {}
                for base in bases:
                    properties.update(deepcopy(models.get(base, {}).get("properties", {})))
                for field in item.body:
                    if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name) and not field.target.id.startswith("_"):
                        rule = _annotation(field.annotation, aliases)
                        rule.update(_field(field.value))
                        if rule:
                            properties[field.target.id] = rule
                if properties:
                    models[item.name] = {"properties": properties, "source": path + ":" + item.name}
        for name, model in api_models.items():
            if model.get("properties"):
                models[name] = {"properties": model["properties"], "source": "OpenAPI:" + name}
        fields = {table: {} for table in table_columns}
        unsupported = {table: [] for table in table_columns}
        for table in database_schema["tables"]:
            for column in table["columns"]:
                name = column["name"].lower()
                if name in {"password", "passwd", "secret", "token", "access_token", "refresh_token", "salt", "signature", "credential", "hash", "digest", "ciphertext"} or name.endswith(("_hash", "_digest", "_ciphertext", "_token", "_secret", "_signature")) or "password" in name or "passwd" in name or name.startswith(("hashed_", "encrypted_")):
                    unsupported[table["name"]].append(column["name"] + " requires application-specific hashing, encryption, or credentials")
                if "BLOB" in (column["type"] or "").upper():
                    unsupported[table["name"]].append(column["name"] + " uses unsupported binary storage")

        for name, problems in model_problems.items():
            for table in _model_tables(name, table_columns):
                unsupported[table].extend(problems)

        def add(table, column, rule, origin):
            if column not in table_columns[table] or not rule:
                return
            problem = _schema_problem(rule, api_models)
            if problem:
                unsupported[table].append(column + ": " + problem)
                return
            entry = {"schema": deepcopy(rule), "source": origin}
            rules = fields[table].setdefault(column, [])
            if entry not in rules:
                rules.append(entry)

        for name, model in models.items():
            for table in _model_tables(name, table_columns):
                unsupported[table].extend(model_problems.get(name, []))
                for column, rule in model["properties"].items():
                    add(table, column, rule, model["source"])
        # Link models/casts used in a query function to its SQL table, including
        # generic names such as RecordOut. This is static inspection, never execution.
        for path, tree, aliases in trees:
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = set()
                for node in ast.walk(function):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        for table in re.findall(r'\b(?:FROM|INTO|UPDATE)\s+["`\[]?([A-Za-z_][A-Za-z0-9_]*)', node.value, re.I):
                            names.update(name for name in table_columns if name.casefold() == table.casefold())
                if len(names) != 1:
                    continue
                table = next(iter(names))
                for node in ast.walk(function):
                    if not isinstance(node, ast.Call):
                        continue
                    model = models.get(_name(node.func).rsplit(".", 1)[-1])
                    unsupported[table].extend(model_problems.get(_name(node.func).rsplit(".", 1)[-1], []))
                    if model:
                        for column, rule in model["properties"].items():
                            add(table, column, rule, model["source"])
                    kind = aliases.get(_name(node.func), _name(node.func)).rsplit(".", 1)[-1]
                    if kind == "UUID" and node.args and isinstance(node.args[0], ast.Subscript):
                        column = _literal(node.args[0].slice)
                        if isinstance(column, str):
                            add(table, column, {"type": "string", "format": "uuid"}, path + ":" + function.name)
        # A referenced ID has the same format throughout a declared FK graph.
        changed = True
        while changed:
            before = json.dumps(fields, sort_keys=True)
            for table in database_schema["tables"]:
                for fk in table["foreign_keys"]:
                    target = next((t for t in database_schema["tables"] if t["name"].casefold() == fk["table"].casefold()), None)
                    if target is None:
                        continue
                    primary = sorted((c for c in target["columns"] if c["pk"]), key=lambda c: c["pk"])
                    column = fk["to"] or (primary[fk["seq"]]["name"] if fk["seq"] < len(primary) else None)
                    left, right = (table["name"], fk["from"]), (target["name"], column)
                    for destination, origin in ((left, right), (right, left)):
                        for entry in list(fields[origin[0]].get(origin[1], [])):
                            if entry["schema"].get("format"):
                                add(*destination, entry["schema"], entry["source"])
            changed = before != json.dumps(fields, sort_keys=True)
        if content_digest(directory) != revision:
            raise ValueError("Application changed while reading mock-data contracts; retry")
        return {"revision": revision, "fields": fields, "source": sources,
                "api_models": api_models, "unsupported_tables": {name: list(dict.fromkeys(reasons)) for name, reasons in unsupported.items() if reasons},
                "coverage": "Simple declared field types, constraints, and formats only. Custom validation, hashes, encryption, and complex storage are refused."}


def _schema_problem(rule, models, seen=frozenset()):
    if not isinstance(rule, dict):
        return "unsupported application field schema"
    try:
        Draft202012Validator.check_schema(rule)
    except (SchemaError, re.error, TypeError, ValueError):
        return "invalid application field schema"
    reference = rule.get("$ref")
    if reference:
        prefix = "#/components/schemas/"
        if not reference.startswith(prefix) or reference in seen:
            return "external or recursive field schemas cannot be mocked"
        name = reference[len(prefix):].replace("~1", "/").replace("~0", "~")
        if name not in models:
            return "unresolved application field schema"
        problem = _schema_problem(models[name], models, seen | {reference})
        if problem:
            return problem
    kind = rule.get("type")
    if kind in {"array", "object"} or isinstance(kind, list) and any(item in {"array", "object"} for item in kind):
        return "nested data needs application-specific serialization"
    if rule.get("format") and rule["format"] not in FormatChecker.checkers:
        return "unsupported application format " + rule["format"]
    supported = {"$ref", "type", "format", "enum", "const", "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "anyOf", "oneOf", "allOf", "title", "description", "default", "example", "examples", "deprecated", "readOnly", "writeOnly"}
    if set(rule) - supported:
        return "complex application field constraints cannot be mocked"
    for composition in ("anyOf", "oneOf", "allOf"):
        for child in rule.get(composition, []):
            problem = _schema_problem(child, models, seen)
            if problem:
                return problem
    return None


def require_mockable(contract, tables):
    for table in tables:
        reasons = contract.get("unsupported_tables", {}).get(table, [])
        if reasons:
            raise GenerationError(
                "This data can't be mocked reliably: " + table + ": " + reasons[0]
                + ". Create these records through the application's API instead. No records were saved.",
                422, code="mock_data_unsupported",
            )


def check_application_row(contract, table, row, index=0):
    require_mockable(contract, [table])
    issues = []
    for column, rules in contract["fields"].get(table, {}).items():
        if column not in row:
            continue
        for entry in rules:
            schema = {"components": {"schemas": contract["api_models"]}, **entry["schema"]}
            value = row[column]
            if schema.get("type") == "boolean" and type(value) is int and value in {0, 1}:
                value = bool(value)
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
            if errors:
                keyword = errors[0].validator
                expected = schema.get("format") or keyword
                issues.append({"table": table, "row": index, "field": column,
                               "expected": expected, "source": entry["source"],
                               "message": f"{table}[{index}].{column} must satisfy {expected} from the application contract"})
                break
    if issues:
        raise DataContractError(issues)
