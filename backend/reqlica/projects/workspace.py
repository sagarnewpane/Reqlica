"""Full versioned workspace archives; only an idle, fenced worker may be captured."""

import hashlib
import io
import os
import shutil
import stat
import zipfile
import zlib
from pathlib import Path
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from reqlica.projects.store import Manifest, Project, ProjectMetadata, registry_directory
from reqlica.projects.transfer import MAX_ARCHIVE, MAX_UNPACKED, MAX_FILES
from reqlica.projects.drafts import _contained
from reqlica.projects.versions import _fsync_directory, _read_code_file, atomic_write_json, code_directory
from reqlica.runtime.sdk.database import backup_database

_EXCLUDED = {"project.lock", "runtime.lock", "scenarios.lock", "activation.json", "activation-backup.sqlite"}


class WorkspaceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["reqlica-workspace-v1"]
    project: ProjectMetadata
    files: dict[str, str] = Field(max_length=MAX_FILES)
    private_environment_included: bool = False


def safe_path(name):
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or path.as_posix() != name or any(ord(c) < 32 for c in name):
        raise ValueError("Unsafe workspace path")
    if path.parts[0] not in {"project.json", "data.sqlite", "versions", "drafts", ".reqlica"}:
        raise ValueError("Unexpected workspace file")
    if path.name in _EXCLUDED or path.name.endswith(("-wal", "-shm", "-journal", ".lock", ".tmp")):
        raise ValueError("Runtime lock/journal files cannot be imported")
    return path


def export_workspace(project, include_secrets=False):
    if project.active_version is None:
        raise ValueError("Convert legacy projects before workspace archival")
    import tempfile
    # .reqlica is intentionally traversed; _code_entries would omit it at root.
    with tempfile.TemporaryDirectory(prefix="reqlica-workspace-") as folder:
        root = Path(folder).resolve()
        files, total = {}, 0
        for parent, dirs, names in os.walk(project.directory, followlinks=False):
            for directory in dirs:
                if (Path(parent) / directory).is_symlink():
                    raise ValueError("Workspace symlinks are unsupported")
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(names):
                relative = (Path(parent) / name).relative_to(project.directory)
                if name in _EXCLUDED or name.endswith(("-wal", "-shm", "-journal", ".lock", ".tmp", ".pyc")):
                    continue
                if relative.as_posix() == ".reqlica/environment.json" and not include_secrets:
                    continue
                safe_path(relative.as_posix())
                checked = _contained(project.directory, relative.as_posix())
                if not checked.is_file() or checked.stat().st_size > MAX_UNPACKED:
                    raise ValueError("Workspace contains an oversized/non-file entry")
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if relative.as_posix() in {"data.sqlite", ".reqlica/request-history.sqlite"}:
                    for suffix in ("-wal", "-shm", "-journal"):
                        _contained(project.directory, relative.as_posix() + suffix)
                    backup_database(project.directory / relative, target)
                else:
                    target.write_bytes(_read_code_file(project.directory, relative))
                total += target.stat().st_size
                if total > MAX_UNPACKED or len(files) >= MAX_FILES:
                    raise ValueError("Workspace exceeds archive limits")
                files[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest = WorkspaceManifest(format="reqlica-workspace-v1", project=project.model_dump(exclude={"directory"}),
                                     files=files, private_environment_included=include_secrets)
        buffer = io.BytesIO()
        if len(manifest.model_dump_json().encode()) > 1024 * 1024:
            raise ValueError("Workspace metadata exceeds 1 MiB")
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("workspace.json", manifest.model_dump_json())
            for name in files:
                archive.write(root / name, name)
                if buffer.tell() > MAX_ARCHIVE:
                    raise ValueError("Workspace archive exceeds 32 MiB")
        if buffer.tell() > MAX_ARCHIVE:
            raise ValueError("Workspace archive exceeds 32 MiB")
        return buffer.getvalue()


def import_workspace(data, directory):
    import tempfile
    from reqlica.projects.transfer import _database_valid
    target = Path(directory).expanduser()
    if not target.is_absolute() or target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("Workspace requires a new absolute destination")
    target = target.parent.resolve() / target.name
    with tempfile.TemporaryDirectory(prefix="reqlica-workspace-import-") as folder:
        root = Path(folder).resolve()
        try:
            if len(data) > MAX_ARCHIVE:
                raise ValueError("Workspace archive exceeds 32 MiB")
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                names = [e.filename for e in entries]
                if len(names) > MAX_FILES + 1 or len(set(names)) != len(names) or sum(e.file_size for e in entries) > MAX_UNPACKED:
                    raise ValueError("Workspace archive exceeds limits or has duplicates")
                if archive.getinfo("workspace.json").file_size > 1024 * 1024:
                    raise ValueError("Workspace metadata exceeds limits")
                metadata = WorkspaceManifest.model_validate_json(archive.read("workspace.json"))
                if set(metadata.files) != set(names) - {"workspace.json"}:
                    raise ValueError("Workspace file index mismatch")
                if not {"project.json", "data.sqlite"} <= set(names):
                    raise ValueError("Workspace is missing project metadata or data")
                if ".reqlica/environment.json" in names and not metadata.private_environment_included:
                    raise ValueError("Workspace secret inclusion flag mismatch")
                for entry in entries:
                    if entry.is_dir() or entry.flag_bits & 1 or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG}:
                        raise ValueError("Only regular unencrypted files are supported")
                    if entry.filename == "workspace.json":
                        continue
                    relative = safe_path(entry.filename)
                    content = archive.read(entry)
                    if hashlib.sha256(content).hexdigest() != metadata.files[entry.filename]:
                        raise ValueError("Workspace checksum mismatch")
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    path.chmod(0o600)
                project = Project(**ProjectMetadata.model_validate_json((root / "project.json").read_bytes()).model_dump(), directory=root)
                if project.model_dump(exclude={"directory"}) != metadata.project.model_dump():
                    raise ValueError("Workspace project identity mismatch")
                code = code_directory(project)
                manifest = Manifest.model_validate_json((code / "manifest.json").read_bytes())
                for endpoint in manifest.endpoints:
                    if not (code / endpoint.handler.split(":")[0]).is_file():
                        raise ValueError("Active workspace handler is missing")
                for path in root.rglob("*.sqlite"):
                    _database_valid(path)
        except (zipfile.BadZipFile, zlib.error, OSError, KeyError, RuntimeError) as error:
            raise ValueError("Invalid workspace archive") from error
        registry = registry_directory()
        registry.mkdir(parents=True, exist_ok=True)
        entry = registry / f"{project.id}.json"
        if entry.exists() or entry.is_symlink():
            raise ValueError("Workspace project ID is already registered")
        created = registered = False
        try:
            target.mkdir(mode=0o700)
            created = True
            shutil.copytree(root, target, dirs_exist_ok=True)
            for path in target.rglob("*"):
                if path.is_file():
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
            _fsync_directory(target)
            _fsync_directory(target.parent)
            temporary = registry / f".workspace-{uuid4().hex}.json"
            try:
                atomic_write_json(temporary, {"directory":str(target)})
                os.link(temporary, entry)
                registered = True
                _fsync_directory(registry)
            finally:
                temporary.unlink(missing_ok=True)
        except BaseException:
            if registered:
                entry.unlink(missing_ok=True)
            if created:
                shutil.rmtree(target)
            raise
        return {"project_id":project.id, "directory":str(target), "status":"imported", "private_environment_included":metadata.private_environment_included}
