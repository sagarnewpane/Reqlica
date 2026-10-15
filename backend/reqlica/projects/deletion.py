"""Conservative project unregistration; filesystem deletion is always explicit."""

import json
import re
import shutil
from pathlib import Path

from reqlica.projects.store import ProjectMetadata, list_project_ids, registry_directory
from reqlica.projects.versions import _read_code_file, project_lock


def _entry(project_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise FileNotFoundError("Unknown project")
    return registry_directory() / f"{project_id}.json"


def inspect_project_deletion(
    project_id: str, delete_files: bool = False, *, allow_missing: bool = False
) -> dict:
    """Describe the target without importing code; stale entries can be unregistered."""
    entry = _entry(project_id)
    registered = entry.exists() or entry.is_symlink()
    if not registered and not allow_missing:
        raise FileNotFoundError(f"Unknown project: {project_id}")
    info = {
        "id": project_id,
        "name": None,
        "directory": None,
        "registered": registered,
        "delete_files": delete_files,
        "error": None,
    }
    directory = None
    try:
        if not registered or entry.is_symlink() or not entry.is_file():
            raise ValueError("Registration is missing or is not a regular file")
        location = json.loads(entry.read_text(encoding="utf-8"))
        directory = Path(location["directory"])
        if not directory.is_absolute():
            raise ValueError("Registered directory must be absolute")
        info["directory"] = str(directory)
        if directory.is_symlink() or directory.resolve() != directory:
            raise ValueError("Refusing file deletion through a symlink or aliased path")
        metadata = ProjectMetadata.model_validate_json(
            _read_code_file(directory, Path("project.json"))
        )
        if metadata.id != project_id:
            raise ValueError("Registered ID does not match project.json")
        info["name"] = metadata.name
    except (OSError, ValueError, KeyError, TypeError) as error:
        info["error"] = str(error)
        if delete_files:
            raise ValueError(
                f"Cannot safely delete project files: {error}. "
                "Omit --delete-files to remove only the registration."
            ) from error
    if delete_files:
        protected = (
            Path.home().resolve(),
            Path.cwd().resolve(),
            Path(__file__).resolve().parents[3],
            registry_directory(),
        )
        if directory == Path(directory.anchor) or any(
            path.is_relative_to(directory) for path in protected
        ):
            raise ValueError("Refusing to delete a protected directory or its ancestor")
        # Never erase another registration's files or leave a shared-directory alias.
        for other_id in list_project_ids():
            if other_id == project_id:
                continue
            try:
                other = json.loads(_entry(other_id).read_text(encoding="utf-8"))
                other_directory = Path(other["directory"])
                if not other_directory.is_absolute():
                    continue
                other_directory = other_directory.resolve()
            except (OSError, ValueError, KeyError, TypeError):
                continue
            same_directory = other_directory.exists() and other_directory.samefile(
                directory
            )
            if (
                same_directory
                or other_directory.is_relative_to(directory)
                or directory.is_relative_to(other_directory)
            ):
                raise ValueError(
                    f"Project directory overlaps registration {other_id}; "
                    "unregister the shared/nested project first, without --delete-files"
                )
    return info


def delete_project_registration(
    project_id: str,
    delete_files: bool = False,
    expected_directory: str | None = None,
    *,
    allow_missing: bool = False,
) -> dict:
    """Called by the gateway only after draining/stopping its project worker."""
    info = inspect_project_deletion(
        project_id, delete_files, allow_missing=allow_missing
    )
    entry = _entry(project_id)
    if delete_files:
        if expected_directory is None or expected_directory != info["directory"]:
            raise ValueError(
                "Project directory changed; inspect and confirm deletion again"
            )
        directory = Path(info["directory"])
        with project_lock(directory):
            current = inspect_project_deletion(project_id, True)
            if current["directory"] != expected_directory:
                raise ValueError("Project directory changed; inspect deletion again")
            from reqlica.runtime.activation import database_guard

            with database_guard(directory):
                # A single checked project directory, never a user-supplied glob/path.
                shutil.rmtree(directory)
    entry.unlink(missing_ok=allow_missing)
    return {
        "id": project_id,
        "name": info["name"],
        "directory": info["directory"],
        "status": "deleted",
        "unregistered": info["registered"],
        "files_deleted": delete_files,
    }
