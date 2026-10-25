"""Bounded user attachments. No remote references are fetched."""
import json
from copy import deepcopy
from pydantic import BaseModel, ConfigDict, Field
from reqlica.validation.contracts import parse_contract

METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}

class Specification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=128, pattern=r"^[^/\\\x00-\x1f]+$")
    content: str = Field(min_length=1, max_length=131072)
    operations: list[str] | None = Field(default=None, min_length=1, max_length=128)


def prepare(spec: Specification, *, require_ready: bool = True) -> dict:
    if len(spec.content.encode()) > 131072:
        raise ValueError("Attachment exceeds 128 KiB")
    document = parse_contract(spec.content)
    # Broken JSON/YAML uploads must not silently become prose requirements.
    if document is None and spec.filename.lower().endswith((".json", ".yaml", ".yml")):
        raise ValueError("Upload a complete OpenAPI 3.0/3.1 document, or use .txt/.md for prose")
    operations = []
    if document is not None:
        operations = [f"{method.upper()} {path}" for path, item in document.get("paths", {}).items()
                      for method in item if method in METHODS]
        if not operations:
            raise ValueError("OpenAPI document has no operations")
        if spec.operations is not None:
            chosen = set(spec.operations)
            if not chosen.issubset(operations):
                raise ValueError("Selected operation is absent from the uploaded document")
            document = deepcopy(document)
            document["paths"] = {
                path: {key: value for key, value in item.items()
                       if key not in METHODS or f"{key.upper()} {path}" in chosen}
                for path, item in document["paths"].items()
                if any(f"{key.upper()} {path}" in chosen for key in item if key in METHODS)
            }
        requirements = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    else:
        if spec.operations is not None:
            raise ValueError("Operation selection requires OpenAPI")
        requirements = spec.content.strip()
    if not requirements:
        raise ValueError("Attachment is empty")
    size = len(requirements.encode())
    ready = size <= 24576
    if require_ready and not ready:
        raise ValueError("Selected specification exceeds the 24 KiB model-context limit; select fewer operations or split the document")
    return {"filename": spec.filename, "kind": "openapi" if document else "requirements",
            "operations": operations, "selected_operations": spec.operations,
            "context_bytes": size, "ready": ready, "requirements": requirements}


def attach(project_id, draft_id, revision, specification):
    from reqlica.projects.store import get_project
    from reqlica.projects.versions import project_lock
    from reqlica.projects.drafts import _state, _save_state
    project = get_project(project_id)
    with project_lock(project.directory):
        state = _state(project, draft_id, revision)
        previous = state.get("generation") or {}
        if previous.get("requirements") == specification["requirements"]:
            return
        state["generation"] = {"requirements": specification["requirements"],
                               "specification": {key: value for key, value in specification.items() if key != "requirements"}}
        state.update(validation=None, test=None, approval=None)
        _save_state(project, draft_id, state)
