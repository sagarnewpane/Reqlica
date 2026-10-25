"""User-owned saved artifacts and reviewable historical code restoration."""
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from typing import Literal
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from reqlica.console.dependencies import draft_operation
from reqlica.projects import drafts
from reqlica.projects.store import get_project
from reqlica.projects.versions import atomic_write_json, code_directory, content_digest, project_lock, snapshot_code, _checked_directory, _read_code_file

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Workspace"])

class SavedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["request", "chain", "dataset"]
    name: str = Field(min_length=1, max_length=100)
    data: dict[str, JsonValue]
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

class SavedDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


def _library(project):
    parent = project.directory / ".reqlica" / "library"
    parent.mkdir(mode=0o700, exist_ok=True)
    return _checked_directory(parent)


def _item_path(project, item_id):
    if not re.fullmatch(r"[a-f0-9]{32}", item_id):
        raise ValueError("Invalid saved item ID")
    return _library(project) / (item_id + ".json")


def list_items(project_id):
    project = get_project(project_id)
    with project_lock(project.directory):
        items = []
        for path in sorted(_library(project).glob("*.json")):
            raw = _read_code_file(project.directory, path.relative_to(project.directory))
            if len(raw) > 270000:
                raise ValueError("Saved item exceeds size limit")
            item = json.loads(raw)
            if not item.get("deleted_at"):
                items.append(item)
        return {"items": sorted(items, key=lambda item: item["updated_at"], reverse=True)}


def save_item(project_id, item_id, body):
    encoded = json.dumps(body.data, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 262144:
        raise ValueError("Saved item exceeds 256 KiB")
    project = get_project(project_id)
    with project_lock(project.directory):
        path = _item_path(project, item_id)
        previous = json.loads(_read_code_file(project.directory, path.relative_to(project.directory))) if path.exists() else None
        if previous and body.expected_revision != previous["revision"]:
            raise ValueError("Saved item changed; refresh before replacing")
        if not previous and body.expected_revision:
            raise ValueError("Saved item no longer exists")
        item = {"id": item_id, "kind": body.kind, "name": body.name.strip(), "data": body.data,
                "updated_at": datetime.now(UTC).isoformat()}
        if not item["name"]:
            raise ValueError("Enter a saved item name")
        item["revision"] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        atomic_write_json(path, item)
        return item


def delete_item(project_id, item_id, body):
    project = get_project(project_id)
    with project_lock(project.directory):
        path = _item_path(project, item_id)
        item = json.loads(_read_code_file(project.directory, path.relative_to(project.directory)))
        if item["revision"] != body.expected_revision:
            raise ValueError("Saved item changed; refresh before removing")
        item["deleted_at"] = datetime.now(UTC).isoformat()
        atomic_write_json(path, item)
        return {"status": "removed", "id": item_id}


@router.get("/library")
def library(project_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(list_items, project_id)

@router.put("/library/{item_id}")
def save(project_id: str, item_id: str, body: SavedItem):
    return draft_operation(save_item, project_id, item_id, body)

@router.delete("/library/{item_id}")
def remove(project_id: str, item_id: str, body: SavedDelete):
    return draft_operation(delete_item, project_id, item_id, body)


def version_history(project_id):
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        if not project.active_version:
            return {"versions": [], "active_revision": content_digest(code_directory(project))}
        parent = _checked_directory(project.directory / "versions")
        versions = []
        for path in parent.iterdir():
            if not re.fullmatch(r"[a-f0-9]{32}", path.name):
                continue
            _checked_directory(path)
            versions.append({"id": path.name, "revision": content_digest(path),
                             "active": path.name == project.active_version,
                             "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()})
        return {"versions": sorted(versions, key=lambda item: item["created_at"], reverse=True),
                "active_revision": content_digest(code_directory(project))}

class VersionRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_version_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


def restore_version(project_id, body):
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        if content_digest(code_directory(project)) != body.expected_revision:
            raise ValueError("Published version changed; refresh history")
        source = _checked_directory(project.directory / "versions" / body.version_id)
        if content_digest(source) != body.expected_version_revision:
            raise ValueError("Historical version changed; refresh history")
        draft = drafts.create_draft(project_id, f"Restore {body.version_id[:8]}")
        target = drafts.draft_directory(project, draft["id"])
        try:
            shutil.rmtree(target)
            snapshot_code(source, target)
            state = drafts._state(project, draft["id"])
            state["restored_from_version"] = body.version_id
            drafts._save_state(project, draft["id"], state)
            return drafts._inspect(project, draft["id"], state)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            drafts._state_path(project, draft["id"]).unlink(missing_ok=True)
            raise

@router.get("/versions")
def versions(project_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(version_history, project_id)

@router.post("/versions/restore")
def restore(project_id: str, body: VersionRestore):
    return draft_operation(restore_version, project_id, body)
