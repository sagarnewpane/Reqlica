"""Move a stopped/fenced project on one filesystem without changing its ID."""

import os
from pathlib import Path
from pydantic import Field
from reqlica.projects.reset import ConfirmedRevision
from reqlica.projects.store import registry_directory, list_project_ids, get_project
from reqlica.projects.versions import _fsync_directory, atomic_write_json, code_directory, content_digest
from reqlica.runtime.maintenance import maintenance


class Relocation(ConfirmedRevision):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_directory: str
    directory: str


def relocate(manager, project_id, body):
    target = Path(body.directory).expanduser()
    if not target.is_absolute() or target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("Destination must be a new absolute directory with an existing parent")
    target = target.parent.resolve() / target.name
    def check(project):
        if str(project.directory) != body.expected_directory or content_digest(code_directory(project)) != body.expected_revision:
            raise ValueError("Project changed; inspect it again before moving")
        if target.is_relative_to(project.directory) or project.directory.is_relative_to(target):
            raise ValueError("Project locations must not overlap")
        if project.directory.stat().st_dev != target.parent.stat().st_dev:
            raise ValueError("Cross-filesystem moves require export/import")
        for identity in list_project_ids():
            if identity == project.id:
                continue
            try:
                other = get_project(identity).directory
            except (OSError, ValueError):
                continue
            if target.is_relative_to(other) or other.is_relative_to(target) or other.is_relative_to(project.directory):
                raise ValueError("Relocation would overlap or invalidate another project registration")
    with maintenance(manager, project_id, check) as project:
        source = project.directory
        # Reserve destination to prevent replacing an existing directory.
        target.mkdir(mode=0o700)
        journal = registry_directory() / f".relocate-{project_id}.json"
        try:
            atomic_write_json(journal, {"source":str(source), "target":str(target)})
            os.rename(source, target)
            try:
                atomic_write_json(registry_directory() / f"{project_id}.json", {"directory":str(target)})
                _fsync_directory(source.parent)
                _fsync_directory(target.parent)
            except BaseException:
                os.rename(target, source)
                raise
        finally:
            if target.exists() and not (target / "project.json").exists():
                target.rmdir()
        journal.unlink(missing_ok=True)
        _fsync_directory(journal.parent)
        with manager._condition:
            manager._stopped[project_id]["directory"] = str(target)
        return {"id":project_id, "directory":str(target), "status":"stopped"}
