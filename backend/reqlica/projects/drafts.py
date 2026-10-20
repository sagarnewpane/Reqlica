"""Digest-based editing and local approval of trusted project code."""

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from reqlica.projects.store import Endpoint, Manifest, Project, get_project
from reqlica.projects.versions import (
    atomic_write_json,
    code_directory,
    content_digest,
    project_lock,
    snapshot_code,
)


def _contained(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or any(ord(character) < 32 or ord(character) == 127 for character in relative)
    ):
        raise ValueError("Path must be a safe project-relative path")
    candidate = root / path
    for component in (root, *candidate.parents, candidate):
        if component.is_symlink():
            raise ValueError("Symlink traversal is not allowed")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes the draft directory")
    return candidate


def draft_directory(project: Project, draft_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", draft_id):
        raise ValueError("Invalid draft ID")
    return _contained(project.directory, f"drafts/{draft_id}")


def _state_path(project: Project, draft_id: str) -> Path:
    draft_directory(project, draft_id)
    return _contained(project.directory, f".reqlica/drafts/{draft_id}.json")


def _save_state(project: Project, draft_id: str, state: dict) -> None:
    state["updated_at"] = datetime.now(UTC).isoformat()
    atomic_write_json(_state_path(project, draft_id), state)


def _draft_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError("Draft name must contain 1 to 100 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("Draft name must be a single line without control characters")
    return name.strip()


def _state(
    project: Project, draft_id: str, expected_revision: str | None = None
) -> dict:
    directory = draft_directory(project, draft_id)
    state = json.loads(_state_path(project, draft_id).read_text(encoding="utf-8"))
    if state["id"] != draft_id:
        raise ValueError("Draft state ID does not match")
    state.setdefault("name", f"Draft {draft_id[:8]}")
    # Legacy drafts have no reliable creation timestamp. Do not invent one.
    state.setdefault("created_at", None)
    state.setdefault(
        "updated_at",
        datetime.fromtimestamp(_state_path(project, draft_id).stat().st_mtime, UTC).isoformat(),
    )
    # project.json marks legacy roots for snapshot helpers, never draft code.
    if (directory / "project.json").exists():
        raise ValueError("Draft code cannot contain reserved project.json")
    revision = content_digest(directory)
    from reqlica.projects.environment import EMPTY_REVISION, read_environment

    environment_revision, _ = read_environment(project.directory)
    environment_changed = any(
        state.get(name)
        and state[name].get("environment_revision", EMPTY_REVISION)
        != environment_revision
        for name in ("validation", "test")
    )
    if state["revision"] != revision or environment_changed:
        state.update(revision=revision, validation=None, test=None, approval=None)
        _save_state(project, draft_id, state)
    if expected_revision is not None and revision != expected_revision:
        raise ValueError("Draft revision conflict; inspect the current draft")
    return state


def _files(directory: Path) -> dict[str, str]:
    files = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        _contained(directory, relative.as_posix())
        if path.is_file():
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _inspect(project: Project, draft_id: str, state: dict) -> dict:
    files = _files(draft_directory(project, draft_id))
    base_project = project.model_copy(update={"active_version": state["base_version"]})
    base = _files(code_directory(base_project))
    return {
        **state,
        "files": sorted(files),
        "changes": {
            "added": sorted(files.keys() - base.keys()),
            "modified": sorted(
                name for name in files.keys() & base.keys() if files[name] != base[name]
            ),
            "deleted": sorted(base.keys() - files.keys()),
        },
    }


def create_draft(project_id: str, name: str | None = None) -> dict:
    if name is not None:
        name = _draft_name(name)
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        if project.active_version is None:
            raise ValueError("Drafts require a versioned project")
        draft_id = uuid4().hex
        directory = draft_directory(project, draft_id)
        revision = snapshot_code(code_directory(project), directory)
        state = {
            "id": draft_id,
            "name": name if name is not None else f"Draft {draft_id[:8]}",
            "created_at": datetime.now(UTC).isoformat(),
            "revision": revision,
            "base_version": project.active_version,
            "validation": None,
            "test": None,
            "approval": None,
        }
        state_path = _state_path(project, draft_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _save_state(project, draft_id, state)
        return _inspect(project, draft_id, state)


def inspect_draft(project_id: str, draft_id: str) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        return _inspect(project, draft_id, _state(project, draft_id))


def rename_draft(project_id: str, draft_id: str, name: str, expected_revision: str) -> dict:
    name = _draft_name(name)
    project = get_project(project_id)
    with project_lock(project.directory):
        state = _state(project, draft_id, expected_revision)
        if state["name"] != name:
            state["name"] = name
            _save_state(project, draft_id, state)
        return _inspect(project, draft_id, state)


def delete_draft(project_id: str, draft_id: str, expected_revision: str, *, confirm: bool) -> dict:
    """Delete unpublished source and its checks; published versions are independent copies."""
    if confirm is not True:
        raise ValueError("Explicit confirmation is required to delete a draft")
    project = get_project(project_id)
    with project_lock(project.directory):
        state = _state(project, draft_id, expected_revision)
        directory = draft_directory(project, draft_id)
        schema = _contained(project.directory, f".reqlica/openapi/drafts/{draft_id}")
        # Validate every path before removing anything. rmtree never follows
        # nested symlinks; the code digest above also rejects unsafe draft entries.
        state_path = _state_path(project, draft_id)
        if schema.exists():
            shutil.rmtree(schema)
        shutil.rmtree(directory)
        state_path.unlink()
        return {"status": "deleted", "id": draft_id, "name": state["name"]}


def _draft_summary(project: Project, state: dict, updated_at: float) -> dict:
    current = state["base_version"] == project.active_version

    def check(name: str) -> dict | None:
        result = state.get(name)
        if not result:
            return None
        if not isinstance(result, dict):
            raise TypeError("Invalid draft check metadata")
        compact = {
            key: result[key]
            for key in ("status", "revision", "tests_run", "tests_passed")
            if key in result
        }
        if isinstance(result.get("schema"), dict):
            compact["schema"] = {
                key: result["schema"][key]
                for key in ("status", "warning_count", "artifact")
                if key in result["schema"]
            }
        return compact

    validation, test = check("validation"), check("test")
    passed = (
        all(
            result
            and result.get("status") == "passed"
            and result.get("revision") == state["revision"]
            for result in (validation, test)
        )
        and test.get("tests_passed", 0) > 0
    )
    approval = state.get("approval")
    if approval is not None and not isinstance(approval, dict):
        raise ValueError("Invalid draft approval metadata")
    approved = passed and (approval or {}).get("revision") == state["revision"]
    database_ready = True
    if approved:
        from reqlica.projects.schema_plan import require_database_ready

        try:
            require_database_ready(
                draft_directory(project, state["id"]),
                _contained(project.directory, "data.sqlite"),
            )
        except (OSError, ValueError):
            database_ready = False
    failed = any(
        result and result.get("status") in {"failed", "error", "timeout"}
        for result in (validation, test)
    )
    stale_checks = any(
        result and result.get("revision") != state["revision"]
        for result in (validation, test)
    )
    if not current:
        progress = "stale_base"
    elif approved:
        progress = "approved"
    elif passed:
        progress = "ready_for_review"
    elif stale_checks:
        progress = "checks_stale"
    elif failed:
        progress = "checks_failed"
    elif validation and validation.get("status") == "passed":
        progress = "validated"
    else:
        progress = "editing"
    return {
        "id": state["id"],
        "name": state["name"],
        "created_at": state["created_at"],
        "updated_at": state["updated_at"],
        "revision": state["revision"],
        "base_version": state["base_version"],
        "base_is_current": current,
        "progress": progress,
        "eligible_for_activation": bool(current and approved and database_ready),
        "state_updated_at": datetime.fromtimestamp(updated_at, UTC).isoformat(),
        "file_count": len(state["files"]),
        "changes": state["changes"],
        "validation": validation,
        "test": test,
        "approval": state.get("approval"),
        "error": None,
    }


def list_drafts(
    project_id: str, *, summary: bool = False, limit: int | None = None, offset: int = 0
) -> dict:
    """Discover saved drafts, including unreadable entries, without executing code.

    Full mode preserves the console/CLI response. Summary mode omits runner logs
    and pages by saved-state modification time; time is not evidence of task intent.
    """
    if offset < 0 or limit is not None and not 1 <= limit <= 100:
        raise ValueError("Draft limit must be 1..100 and offset must be nonnegative")
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        directory = _contained(project.directory, ".reqlica/drafts")
        paths = [
            path
            for path in directory.glob("*.json")
            if re.fullmatch(r"[0-9a-f]{32}", path.stem)
        ]
        times = {}
        for path in paths:
            try:
                times[path.stem] = path.lstat().st_mtime
            except OSError:
                times[path.stem] = 0.0
        paths.sort(
            key=lambda path: (-times[path.stem], path.stem) if summary else path.stem
        )
        total = len(paths)
        selected = paths[offset : None if limit is None else offset + limit]
        result = []
        for path in selected:
            try:
                state = inspect_draft(project_id, path.stem)
                updated = path.lstat().st_mtime
                result.append(
                    _draft_summary(project, state, updated) if summary else state
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                result.append(
                    {"id": path.stem, "error": str(error), "progress": "unreadable"}
                )
        payload = {"drafts": result}
        if summary or limit is not None or offset:
            next_offset = offset + len(selected)
            payload.update(
                total=total,
                offset=offset,
                limit=limit,
                next_offset=next_offset if next_offset < total else None,
            )
        return payload


def _editable_path(directory: Path, path: str) -> Path:
    target = _contained(directory, path)
    parts = target.relative_to(directory).parts
    if not parts or any(
        part
        in {".reqlica", ".git", "drafts", "versions", "__pycache__", "project.json"}
        or part.endswith((".sqlite", ".sqlite3", ".db", ".pyc", "-wal", "-shm"))
        for part in parts
    ):
        raise ValueError("Reserved control or database path")
    if target.exists() and not target.is_file():
        raise ValueError("Path must name a regular file")
    return target


def _finish_edit(project: Project, draft_id: str, state: dict) -> dict:
    state.update(
        revision=content_digest(draft_directory(project, draft_id)),
        validation=None,
        test=None,
        approval=None,
    )
    _save_state(project, draft_id, state)
    return _inspect(project, draft_id, state)


def write_draft_file(
    project_id: str, draft_id: str, path: str, content: str, expected_revision: str
) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        state = _state(project, draft_id, expected_revision)
        target = _editable_path(draft_directory(project, draft_id), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return _finish_edit(project, draft_id, state)


def delete_draft_file(
    project_id: str, draft_id: str, path: str, expected_revision: str
) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        state = _state(project, draft_id, expected_revision)
        _editable_path(draft_directory(project, draft_id), path).unlink()
        return _finish_edit(project, draft_id, state)


def upsert_endpoint(
    project_id: str, draft_id: str, endpoint: Endpoint, expected_revision: str
) -> dict:
    project = get_project(project_id)
    endpoint = Endpoint.model_validate(endpoint)
    with project_lock(project.directory):
        _state(project, draft_id, expected_revision)
        manifest_path = _editable_path(
            draft_directory(project, draft_id), "manifest.json"
        )
        manifest = Manifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        endpoints = [
            item
            for item in manifest.endpoints
            if (item.method, item.path) != (endpoint.method, endpoint.path)
        ]
        updated = Manifest(endpoints=[*endpoints, endpoint])
        return write_draft_file(
            project_id,
            draft_id,
            "manifest.json",
            updated.model_dump_json(indent=2) + "\n",
            expected_revision,
        )


def remove_endpoint(
    project_id: str, draft_id: str, method: str, path: str, expected_revision: str
) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        _state(project, draft_id, expected_revision)
        manifest_path = _editable_path(
            draft_directory(project, draft_id), "manifest.json"
        )
        manifest = Manifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        endpoints = [
            item
            for item in manifest.endpoints
            if (item.method, item.path) != (method, path)
        ]
        if len(endpoints) == len(manifest.endpoints):
            raise ValueError("Endpoint not found")
        updated = Manifest(endpoints=endpoints)
        return write_draft_file(
            project_id,
            draft_id,
            "manifest.json",
            updated.model_dump_json(indent=2) + "\n",
            expected_revision,
        )


def _require_checks(state: dict) -> None:
    for name in ("validation", "test"):
        result = state.get(name)
        if (
            not result
            or result.get("status") != "passed"
            or result.get("revision") != state["revision"]
        ):
            raise ValueError("Validation and tests must pass for this exact revision")
    if (
        state["test"].get("tests_run", 0) < 1
        or state["test"].get("tests_passed", 0) < 1
    ):
        raise ValueError("At least one passing test is required")


def approve_draft(project_id: str, draft_id: str, expected_revision: str) -> dict:
    """Record explicit caller approval, not authentication or a security boundary."""
    project = get_project(project_id)
    with project_lock(project.directory):
        state = _state(project, draft_id, expected_revision)
        _require_checks(state)
        state["approval"] = {"revision": state["revision"]}
        _save_state(project, draft_id, state)
        return _inspect(project, draft_id, state)


def require_approved(project_id: str, draft_id: str, expected_revision: str) -> dict:
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        state = _state(project, draft_id, expected_revision)
        _require_checks(state)
        if (
            not state.get("approval")
            or state["approval"].get("revision") != state["revision"]
        ):
            raise ValueError("Explicit approval is required for this revision")
        if state["base_version"] != project.active_version:
            raise ValueError("Draft base version is no longer active")
        from reqlica.projects.schema_plan import require_database_ready

        require_database_ready(
            draft_directory(project, draft_id),
            _contained(project.directory, "data.sqlite"),
        )
        return _inspect(project, draft_id, state)


def validate_draft(project_id: str, draft_id: str, expected_revision: str) -> dict:
    from reqlica.validation.checks import validate_draft as validate

    return validate(project_id, draft_id, expected_revision)


def test_draft(project_id: str, draft_id: str, expected_revision: str) -> dict:
    from reqlica.validation.checks import test_draft as test

    return test(project_id, draft_id, expected_revision)


def call_draft_endpoint(
    project_id: str,
    draft_id: str,
    expected_revision: str,
    method: str,
    path: str,
    json_body=None,
) -> dict:
    from reqlica.validation.checks import call_draft_endpoint as call

    return call(project_id, draft_id, expected_revision, method, path, json_body)
