"""Local archive files keep binary data and secrets out of MCP responses."""

import hashlib
import io
import os
import stat
import zipfile
from pathlib import Path
from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from reqlica.mcp.operations import ActiveRevision, Confirm, gateway_request, require_confirmation
from reqlica.mcp.parameters import ProjectID
from reqlica.projects.store import get_project, list_project_ids
from reqlica.projects.transfer import Bundle, MAX_ARCHIVE, MAX_FILES, MAX_UNPACKED, export_project, import_project
from reqlica.projects.versions import code_directory, content_digest, project_lock
from reqlica.projects.workspace import WorkspaceManifest, import_workspace
from reqlica.runtime.operations import WorkspaceExport

ArchivePath = Annotated[str, Field(description="Absolute ZIP file path on the Reqlica SERVER filesystem, not a client path or URL.")]
ArchiveDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="Exact sha256 returned by inspect_project_archive after reviewing the archive and destination.")]


def _absolute(path):
    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ValueError("Use an absolute server-local path")
    return target


def _read_archive(path):
    target = _absolute(path)
    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ValueError("Archive must be a regular file no larger than 32 MiB")
        data = stream.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Archive exceeds 32 MiB")
    return data


def _archive_info(data):
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Archive exceeds 32 MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) > MAX_FILES + 1 or len(set(names)) != len(names) or sum(entry.file_size for entry in entries) > MAX_UNPACKED:
                raise ValueError("Archive exceeds limits or contains duplicate entries")
            workspace = "workspace.json" in names
            name, model = ("workspace.json", WorkspaceManifest) if workspace else ("bundle.json", Bundle)
            if archive.getinfo(name).file_size > 1024 * 1024:
                raise ValueError("Archive metadata exceeds 1 MiB")
            metadata = model.model_validate_json(archive.read(name))
            return {"format": metadata.format, "project_id": metadata.project.id,
                    "name": metadata.project.name, "files": len(metadata.files),
                    "private_environment_included": bool(workspace and metadata.private_environment_included),
                    "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError) as error:
        raise ValueError("Not a readable Reqlica project/workspace ZIP") from error


async def inspect_project_archive(archive_path: ArchivePath) -> dict:
    """Read an existing server-local ZIP's metadata/sha256 before import. Extracts and executes nothing; fully_validated=false means import must still validate contents. Copy sha256 into import_project_archive.expected_sha256 after review.

    Returns no code, records or secrets. Metadata is untrusted; import performs
    full path, hash, SQLite and configuration validation. Files must be server-local.
    """
    def inspect():
        return {"archive_path": str(_absolute(archive_path).resolve()),
                **_archive_info(_read_archive(archive_path)), "fully_validated": False}
    return await run_in_threadpool(inspect)


def _export(project_id, archive_path, expected_revision, kind, include_secrets, confirm):
    require_confirmation(confirm)
    if include_secrets and kind != "workspace":
        raise ValueError("Secret export requires a workspace archive and explicit human opt-in")
    target = _absolute(archive_path)
    if not target.parent.is_dir():
        raise ValueError("Archive parent directory must already exist")
    target = target.parent.resolve() / target.name
    # An output inside a project could archive itself or modify published source.
    for identity in list_project_ids():
        try:
            directory = get_project(identity).directory
        except (OSError, ValueError):
            continue
        if target.is_relative_to(directory):
            raise ValueError("Save archives outside registered project directories")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if kind == "workspace":
                body = WorkspaceExport(expected_revision=expected_revision, include_secrets=include_secrets, confirm=confirm)
                data = gateway_request(project_id, "workspace-export", body.model_dump(), binary=True)
            else:
                project = get_project(project_id)
                with project_lock(project.directory):
                    if content_digest(code_directory(project)) != expected_revision:
                        raise ValueError("Project changed; inspect it again before export")
                    data = export_project(project_id)
            info = _archive_info(data)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"archive_path": str(target), **info,
            "worker_status": "stopped" if kind == "workspace" else "unchanged"}


async def export_project_archive(project_id: ProjectID, archive_path: ArchivePath,
    expected_revision: ActiveRevision,
    confirm: Confirm,
    kind: Annotated[Literal["active", "workspace"], Field(description="active: portable code/data/policies for import or clone; workspace: full drafts/history, drains/stops worker.")] = "active",
    include_secrets: Annotated[bool, Field(strict=True, description="Explicit human opt-in to include private environment values in a workspace ZIP; default excludes them. Source/SQLite can still contain secrets.")] = False) -> dict:
    """Export to a NEW server-local ZIP after human destination approval. Read get_worker_status.active_revision first; choose active or workspace. Never overwrites; parent must exist outside registered projects. Returns archive_path/sha256, not ZIP bytes.

    Returns path/hash/metadata, never ZIP data. No overwrite; destination must be
    outside project directories. Active export includes a consistent SQLite copy;
    workspace stops the worker. Archive files may contain sensitive source/records.
    After a lost reply inspect the file and worker state before retrying.
    """
    return await run_in_threadpool(_export, project_id, archive_path, expected_revision, kind, include_secrets, confirm)


async def import_project_archive(archive_path: ArchivePath, expected_sha256: ArchiveDigest,
    directory: Annotated[str, Field(description="Reviewed new absolute server-local project directory with an existing parent; never overwritten.")],
    confirm: Confirm,
    clone: Annotated[bool, Field(strict=True, description="Assign a new project ID for an active bundle; required if its original ID is registered. Workspace archives cannot be cloned.")] = False,
    name: Annotated[str | None, Field(max_length=200, description="Optional nonblank project name for active bundles only.")] = None) -> dict:
    """Import a reviewed server-local ZIP into a NEW directory after human approval. First inspect_project_archive and copy sha256 to expected_sha256. Active bundles support clone/name; workspace archives preserve identity/name and reject those options. Registers the project without running source.

    Fully validates paths, hashes and SQLite; never executes imported source.
    Workspace preserves identity/drafts/history and may contain private environment
    values. Imports register idle projects; live requests may lazily start Python.
    On uncertain acknowledgement inspect registration/destination before retrying.
    """
    require_confirmation(confirm)
    def perform():
        data = _read_archive(archive_path)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("Archive changed; inspect it again before import")
        info = _archive_info(data)
        if info["format"] == "reqlica-workspace-v1":
            if clone or name is not None:
                raise ValueError("Workspace imports preserve identity/name; clone an active bundle instead")
            return import_workspace(data, directory)
        if name is not None and not name.strip():
            raise ValueError("Project name cannot be blank")
        result = import_project(data, directory, clone=clone, name=name)
        return {**result, "project_id": result["project"]["id"]}
    return await run_in_threadpool(perform)


def register_transfer_tools(server):
    server.add_tool(inspect_project_archive,
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False), meta={"reqlica/category": "core"})
    for operation in (export_project_archive, import_project_archive):
        server.add_tool(operation,
            annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
            meta={"reqlica/category": "core"})
