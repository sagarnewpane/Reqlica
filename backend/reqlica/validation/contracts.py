"""Independent, bounded checks against the user's original OpenAPI contract.

No route-name heuristics or provider-generated expectations. Unknown constructs
are coverage gaps, not evidence of conformance. Never resolve remote references.
"""

import copy
import json
import re
from contextlib import contextmanager
from unittest.mock import patch

import yaml
from fastapi.openapi.models import OpenAPI
from fastapi.testclient import TestClient
from jsonschema import Draft4Validator, Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource

METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
LIMIT = 20


class UnsupportedContract(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key, value in loader.construct_pairs(node, deep=deep):
        if not isinstance(key, (str, int)) or isinstance(key, bool):
            raise TypeError("Contract contains invalid mapping keys")
        key = str(key)
        if key in result:
            raise ValueError("Contract contains invalid or duplicate mapping keys")
        result[key] = value
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_contract(requirements: str) -> dict | None:
    """Accept a complete JSON/YAML document (optionally fenced), not prose guesses."""
    text = requirements.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not re.search(r'(?m)^\s*(?:openapi\s*:|"openapi"\s*:)', text) and not (
        text.startswith("{") and '"openapi"' in text
    ):
        return None
    if len(text.encode()) > 131072:
        raise ValueError("Original OpenAPI exceeds contract check size limit")
    try:
        document = yaml.load(text, Loader=UniqueLoader)
        _bounded(document)
        # Reject recursive/alias expansion and non-JSON YAML types before traversal.
        encoded = json.dumps(document, allow_nan=False)
        if len(encoded) > 262144:
            raise ValueError("Expanded contract exceeds size limit")
        document = json.loads(encoded)
        if not str(document.get("openapi", "")).startswith(("3.0.", "3.1.")):
            raise ValueError("Only OpenAPI 3.0 and 3.1 contracts are supported")
        OpenAPI.model_validate(document)
    except (ValueError, TypeError, AttributeError, RecursionError, yaml.YAMLError) as error:
        raise ValueError("Original OpenAPI is invalid or exceeds check limits") from error
    return document


def _bounded(document):
    remaining = 25000
    expanded_size = 0

    def visit(value, depth=0):
        nonlocal remaining, expanded_size
        remaining -= 1
        expanded_size += len(value.encode()) if isinstance(value, str) else 8
        if remaining < 0 or depth > 60 or expanded_size > 262144:
            raise ValueError("Contract complexity exceeds check limits")
        if isinstance(value, dict):
            for key, child in value.items():
                expanded_size += len(str(key).encode())
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    visit(document)


def resolve(document, value):
    seen = set()
    while isinstance(value, dict) and "$ref" in value:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise UnsupportedContract("External references are not fetched")
        if reference in seen:
            raise UnsupportedContract("Recursive reference needs explicit tests")
        seen.add(reference)
        target = document
        try:
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError) as error:
            raise ValueError("Unresolvable local contract reference") from error
        # 3.1 $ref siblings may impose constraints; do not silently discard them.
        if set(value) - {"$ref", "summary", "description"}:
            raise UnsupportedContract("Reference siblings need explicit tests")
        value = target
    if not isinstance(value, dict):
        raise UnsupportedContract("Non-object schema needs explicit tests")
    return value


def operations(document):
    for path, item in document.get("paths", {}).items():
        item = resolve(document, item)
        for method, operation in item.items():
            if method in METHODS:
                yield method, path, item, operation


def _narrowing(expected, actual, original, generated, location, depth=0):
    """Prove simple input restrictions, rather than inventing arbitrary valid data."""
    if depth > 20:
        raise UnsupportedContract("Nested input comparison limit reached")
    expected, actual = resolve(original, expected), resolve(generated, actual)
    if any(key in schema for schema in (expected, actual) for key in ("anyOf", "oneOf", "allOf", "not", "if")):
        raise UnsupportedContract("Composed input schemas need explicit boundary tests")
    issues = []
    et, at = expected.get("type"), actual.get("type")
    if et and at and et != at:
        # A number input accepting integer-only values is narrower, but vice versa isn't.
        if not (et == "integer" and at == "number"):
            issues.append(f"{location}: generated input type differs from original")
        return issues
    if expected.get("nullable") and not actual.get("nullable"):
        issues.append(f"{location}: generated input rejects allowed null")
    for key in ("minLength", "minimum", "minItems", "minProperties"):
        default = 0 if key in {"minLength", "minItems", "minProperties"} else float("-inf")
        if key in actual and actual[key] > expected.get(key, default):
            issues.append(f"{location}: generated {key} is stricter than original")
    for key in ("maxLength", "maximum", "maxItems", "maxProperties"):
        if key in actual and (key not in expected or actual[key] < expected[key]):
            issues.append(f"{location}: generated {key} is stricter than original")
    if "enum" in expected and "enum" in actual and any(
        value not in actual["enum"] for value in expected["enum"]
    ):
        issues.append(f"{location}: generated enum excludes declared inputs")
    if actual.get("required") and set(actual["required"]) - set(expected.get("required", [])):
        issues.append(f"{location}: generated input adds required fields")
    for name, schema in expected.get("properties", {}).items():
        if isinstance(schema, dict) and schema.get("readOnly"):
            continue
        if name in actual.get("properties", {}):
            issues.extend(_narrowing(schema, actual["properties"][name], original, generated, f"{location}.{name}", depth + 1))
        elif actual.get("additionalProperties") is False:
            issues.append(f"{location}.{name}: generated input forbids a declared field")
    if isinstance(expected.get("items"), dict) and isinstance(actual.get("items"), dict):
        issues.extend(_narrowing(expected["items"], actual["items"], original, generated, location + "[]", depth + 1))
    return issues


def _json_schema(document):
    """Translate OAS 3.0 nullable; preserve 3.1's native JSON Schema semantics."""
    result = copy.deepcopy(document)

    def reference(value):
        if "$ref" in value and not str(value["$ref"]).startswith("#/"):
            raise UnsupportedContract("External references are not fetched")

    def schema(value):
        if not isinstance(value, dict):
            return
        reference(value)
        if any(isinstance(value.get(key), str) for key in ("$dynamicRef", "$recursiveRef", "$id", "$anchor")):
            raise UnsupportedContract("Named or dynamic references need explicit tests")
        for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            for child in value.get(key, {}).values():
                schema(child)
        for key in ("items", "contains", "additionalProperties", "not", "if", "then", "else", "propertyNames", "unevaluatedProperties"):
            schema(value.get(key))
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            for child in value.get(key, []):
                schema(child)
        if str(document["openapi"]).startswith("3.0") and value.get("nullable") is True and "type" in value:
            value["type"] = [value["type"], "null"]
        if "properties" in value and "required" in value:
            value["required"] = [
                name for name in value["required"]
                if not (isinstance(value["properties"].get(name), dict) and value["properties"][name].get("writeOnly"))
            ]

    def visit(value):
        if isinstance(value, dict):
            reference(value)
            for key, child in value.items():
                if key in {"example", "examples", "default", "enum", "const"}:
                    continue
                if key == "schema":
                    schema(child)
                elif key == "schemas":
                    for definition in child.values():
                        schema(definition)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return result


class ContractChecks:
    def __init__(self, document):
        self.document = document
        self.failures = []
        self.warnings = []
        self.exercised = set()
        self.responses_checked = 0
        self.routes = []
        self.validator = None
        self.response_document = document
        try:
            self.routes = sorted(operations(document), key=lambda route: route[1].count("{"))
            schema = _json_schema(document)
            self.response_document = schema
            validator = Draft4Validator if document["openapi"].startswith("3.0") else Draft202012Validator
            def deny_remote(uri):
                raise NoSuchResource(ref=uri)

            self.validator = validator(schema, format_checker=FormatChecker(), registry=Registry(retrieve=deny_remote))
        except UnsupportedContract as error:
            self.warn(str(error))

    def warn(self, message):
        if message not in self.warnings and len(self.warnings) < LIMIT:
            self.warnings.append(message)

    def fail(self, label, message):
        failure = {"test": f"contract {label}", "kind": "contract", "output": message[:1024]}
        if failure not in self.failures and len(self.failures) < LIMIT:
            self.failures.append(failure)

    def compare_inputs(self, generated):
        for method, path, item, operation in self.routes:
            label = f"{method.upper()} {path}"
            actual_item = generated.get("paths", {}).get(path, {})
            actual = actual_item.get(method)
            if actual is None:
                self.fail(label, "Original contract route is not registered")
                continue
            try:
                original_body = resolve(self.document, operation.get("requestBody", {}))
                actual_body = resolve(generated, actual.get("requestBody", {}))
                if actual_body.get("required") and not original_body.get("required"):
                    self.fail(label, "Generated route requires an optional request body")
                expected_schema = original_body.get("content", {}).get("application/json", {}).get("schema")
                actual_schema = actual_body.get("content", {}).get("application/json", {}).get("schema")
                if expected_schema is not None and actual_schema is not None:
                    for issue in _narrowing(expected_schema, actual_schema, self.document, generated, "body"):
                        self.fail(label, issue)
                elif expected_schema is not None:
                    self.warn(f"{label}: generated JSON body schema unavailable")
                expected_parameters = [resolve(self.document, p) for p in item.get("parameters", []) + operation.get("parameters", [])]
                actual_parameters = [resolve(generated, p) for p in actual_item.get("parameters", []) + actual.get("parameters", [])]
                expected_map = {(p["in"], p["name"].lower() if p["in"] == "header" else p["name"]): p for p in expected_parameters}
                security_parameters = set()
                for requirement in operation.get("security", self.document.get("security", [])):
                    for name in requirement:
                        scheme = self.document.get("components", {}).get("securitySchemes", {}).get(name, {})
                        if scheme.get("type") in {"http", "oauth2", "openIdConnect"}:
                            security_parameters.add(("header", "authorization"))
                        elif scheme.get("type") == "apiKey":
                            name = scheme.get("name", "")
                            security_parameters.add((scheme.get("in"), name.lower() if scheme.get("in") == "header" else name))
                for parameter in actual_parameters:
                    key = (parameter["in"], parameter["name"].lower() if parameter["in"] == "header" else parameter["name"])
                    expected = expected_map.get(key)
                    if parameter.get("required") and not (expected or {}).get("required") and key not in security_parameters:
                        self.fail(label, f"{key[0]}: generated route adds a required parameter")
                    if expected and "schema" in expected and "schema" in parameter:
                        for issue in _narrowing(expected["schema"], parameter["schema"], self.document, generated, key[0] + "." + key[1]):
                            self.fail(label, issue)
            except UnsupportedContract as error:
                self.warn(f"{label}: {error}")

    def observe(self, response):
        request = response.request
        for method, path, _, operation in self.routes:
            pattern = re.escape(path)
            pattern = re.sub(r"\\\{[^{}]+\\\}", "[^/]+", pattern)
            if method.upper() != request.method or not re.fullmatch(pattern, request.url.path):
                continue
            label = f"{method.upper()} {path}"
            self.exercised.add(label)
            status = str(response.status_code)
            response_item = resolve(self.response_document, self.response_document["paths"][path])
            responses = response_item[method].get("responses", {})
            declaration = responses.get(status, responses.get(status[0] + "XX", responses.get("default")))
            if declaration is None:
                self.fail(label, f"Undeclared response status {status}")
                return
            try:
                declaration = resolve(self.response_document, declaration)
                if self.validator is not None:
                    for name, header in declaration.get("headers", {}).items():
                        header = resolve(self.response_document, header)
                        if name in response.headers and "schema" in header:
                            value = response.headers[name]
                            header_schema = resolve(self.response_document, header["schema"])
                            if header_schema.get("type") in {"integer", "number", "boolean"}:
                                try:
                                    value = json.loads(value)
                                except ValueError:
                                    pass
                            for error in self.validator.evolve(schema=header["schema"]).iter_errors(value):
                                self.fail(label, f"Response {status} header {name} violates {error.validator}")
                content = declaration.get("content", {})
                if response.content and content:
                    media = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    entry = content.get(media, content.get(media.split("/")[0] + "/*", content.get("*/*")))
                    if entry is None:
                        self.fail(label, f"Response {status} has undeclared media type")
                    elif media == "application/json" or media.endswith("+json"):
                        if self.validator is not None and "schema" in entry:
                            try:
                                value = response.json()
                            except ValueError:
                                self.fail(label, f"Response {status} is not valid JSON")
                            else:
                                # Evolve retains the original document as reference root.
                                for error in self.validator.evolve(schema=entry["schema"]).iter_errors(value):
                                    # Do not include instance values (tokens/passwords/PII).
                                    self.fail(label, f"Response {status} violates {error.validator} at schema path " + "/".join(map(str, error.absolute_schema_path)))
                                    if len(self.failures) >= LIMIT:
                                        break
                                self.responses_checked += 1
                        else:
                            self.warn(f"{label}: response schema check unavailable")
                    else:
                        self.warn(f"{label}: non-JSON response needs explicit tests")
                elif content and not response.content and request.method != "HEAD":
                    self.fail(label, f"Response {status} has no declared body")
                elif status == "204" and response.content:
                    self.fail(label, "Response 204 must not contain a body")
                elif response.content and not content:
                    self.warn(f"{label}: response body has no original content schema")
                return
            except UnsupportedContract as error:
                self.warn(f"{label}: {error}")
                return

    @contextmanager
    def monitor(self):
        send = TestClient.send

        def observed_send(client, *args, **kwargs):
            response = send(client, *args, **kwargs)
            self.observe(response)
            return response

        with patch.object(TestClient, "send", observed_send):
            yield

    def report(self):
        declared = {f"{method.upper()} {path}" for method, path, _, _ in self.routes}
        unexercised = sorted(declared - self.exercised)
        if unexercised:
            self.warn("Some contract routes were not exercised by HTTP tests")
        return {
            "status": "failed" if self.failures else "partial",
            "responses_checked": self.responses_checked,
            "routes_exercised": sorted(self.exercised)[:100],
            "routes_unexercised": unexercised[:100],
            "warnings": self.warnings,
            "limitations": "Partial checks only: simple declared input restrictions and observed TestClient JSON responses. Patterns, exclusive bounds, custom validation, normalization, unobserved responses and business workflows require explicit tests.",
        }
