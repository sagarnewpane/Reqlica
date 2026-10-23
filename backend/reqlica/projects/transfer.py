"""Validated portable bundles of the active project; import never executes source."""

import hashlib
import io
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
import zlib
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from reqlica.projects.authentication import StoredAuth, _read as read_auth
from reqlica.projects.cors import CorsSettings, read_cors
from reqlica.projects.drafts import _contained
from reqlica.projects.environment import environment_metadata
from reqlica.projects.reset import Baseline, _baseline, baseline_file
from reqlica.projects.scenarios import ScenarioSettings, ScenarioState, _read as read_scenarios, _scenario_lock
from reqlica.projects.store import Manifest, Project, ProjectMetadata, get_project, registry_directory
from reqlica.projects.versions import _code_entries, _fsync_directory, _read_code_file, atomic_write_json, code_directory, content_digest, project_lock, snapshot_code
from reqlica.runtime.sdk.database import backup_database

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
MAX_FILES = 2000


class Bundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["reqlica-project-v1"]
    project: ProjectMetadata
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, str] = Field(max_length=MAX_FILES)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: ScenarioSettings
    mock_auth: StoredAuth
    environment_names: list[str] = Field(max_length=100)
    baseline: Baseline | None = None
    cors: CorsSettings = Field(default_factory=CorsSettings)


def _safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or path.as_posix() != name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Bundle contains an unsafe path")
    if name in {"data.sqlite", "settings.json", "baseline.sqlite"}:
        return name
    if not name.startswith("code/") or len(path.parts) < 2 or path.parts[1] in {"project.json", ".reqlica", "versions", "drafts"}:
        raise ValueError("Unexpected bundle file")
    return name


def _database_valid(path):
    try:
        # These are closed, self-contained SQLite backup snapshots, never live DBs.
        # Immutable reads avoid creating WAL/SHM files beside portable snapshots.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Bundle SQLite integrity check failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Bundle SQLite foreign-key check failed")
    except sqlite3.Error as error:
        raise ValueError("Bundle contains an invalid SQLite database") from error


def export_project(project_id):
    project = get_project(project_id)
    with tempfile.TemporaryDirectory(prefix="reqlica-export-") as folder, project_lock(project.directory):
        root = Path(folder).resolve()
        project = get_project(project_id)
        code = code_directory(project)
        source_files = [code / relative for relative, is_directory in _code_entries(code) if not is_directory]
        if len(source_files) > MAX_FILES - 4 or sum(path.stat().st_size for path in source_files) > MAX_UNPACKED:
            raise ValueError("Project source exceeds bundle limits")
        revision = snapshot_code(code, root / "code")
        source = _contained(project.directory, "data.sqlite")
        database_size = source.stat().st_size
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = _contained(project.directory, "data.sqlite" + suffix)
            if sidecar.exists():
                database_size += sidecar.stat().st_size
        if database_size > MAX_UNPACKED:
            raise ValueError("Project database exceeds bundle limits")
        try:
            backup_database(source, root / "data.sqlite")
        except (sqlite3.Error, TimeoutError) as error:
            raise ValueError("Cannot snapshot the project database") from error
        _database_valid(root / "data.sqlite")
        _, auth = read_auth(project.directory)
        with _scenario_lock(project.directory):
            scenario = read_scenarios(project.directory)
        baseline = _baseline(project)
        if baseline:
            if baseline_file(project, baseline.revision).stat().st_size > MAX_UNPACKED:
                raise ValueError("Baseline exceeds bundle limits")
            raw = _read_code_file(project.directory, baseline_file(project, baseline.revision).relative_to(project.directory))
            if hashlib.sha256(raw).hexdigest() != baseline.digest:
                raise ValueError("Baseline snapshot is modified")
            (root / "baseline.sqlite").write_bytes(raw)
        settings = Settings(scenarios=ScenarioSettings(rules=scenario.rules), mock_auth=auth,
                            environment_names=environment_metadata(project.directory)["names"], baseline=baseline,
                            cors=read_cors(project.directory)[1])
        (root / "settings.json").write_text(settings.model_dump_json())
        files = {}
        total = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            name = _safe_name(path.relative_to(root).as_posix())
            total += path.stat().st_size
            if total > MAX_UNPACKED or len(files) >= MAX_FILES:
                raise ValueError("Project exceeds bundle size/file limits")
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        metadata = project.model_dump(exclude={"directory"})
        metadata["active_version"] = project.active_version or uuid4().hex
        bundle = Bundle(format="reqlica-project-v1", project=metadata, revision=revision, files=files)
        if len(bundle.model_dump_json().encode()) > 1024 * 1024:
            raise ValueError("Bundle metadata exceeds 1 MiB")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bundle.json", bundle.model_dump_json())
            for name in files:
                archive.write(root / name, name)
                if buffer.tell() > MAX_ARCHIVE:
                    raise ValueError("Compressed bundle exceeds 32 MiB")
        if buffer.tell() > MAX_ARCHIVE:
            raise ValueError("Compressed bundle exceeds 32 MiB")
        return buffer.getvalue()


@contextmanager
def validated_bundle(data):
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Bundle exceeds 32 MiB")
    with tempfile.TemporaryDirectory(prefix="reqlica-import-") as folder:
        root = Path(folder).resolve()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_FILES + 1 or sum(i.file_size for i in entries) > MAX_UNPACKED:
                    raise ValueError("Bundle exceeds unpacked size/file limits")
                names = [item.filename for item in entries]
                if len(set(names)) != len(names) or names.count("bundle.json") != 1:
                    raise ValueError("Bundle has duplicate entries or missing metadata")
                if archive.getinfo("bundle.json").file_size > 1024 * 1024:
                    raise ValueError("Bundle metadata is too large")
                bundle = Bundle.model_validate_json(archive.read("bundle.json"))
                if set(bundle.files) != set(names) - {"bundle.json"}:
                    raise ValueError("Bundle file index does not match archive")
                if not {"code/manifest.json", "data.sqlite", "settings.json"} <= set(names):
                    raise ValueError("Bundle is missing required files")
                if bundle.project.active_version is None:
                    raise ValueError("Bundle must declare an active version")
                for entry in entries:
                    mode = entry.external_attr >> 16
                    if entry.is_dir() or entry.flag_bits & 1 or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                        raise ValueError("Only regular unencrypted bundle files are supported")
                    if entry.filename == "bundle.json":
                        continue
                    name = _safe_name(entry.filename)
                    raw = archive.read(entry)
                    if hashlib.sha256(raw).hexdigest() != bundle.files[name]:
                        raise ValueError("Bundle file checksum mismatch")
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        stream.write(raw)
                if content_digest(root / "code") != bundle.revision:
                    raise ValueError("Bundle source revision mismatch")
                manifest = Manifest.model_validate_json((root / "code/manifest.json").read_bytes())
                for endpoint in manifest.endpoints:
                    handler = _contained(root / "code", endpoint.handler.split(":")[0])
                    if not handler.is_file():
                        raise ValueError("Bundle handler file is missing")
                _database_valid(root / "data.sqlite")
                settings = Settings.model_validate_json((root / "settings.json").read_bytes())
                if (settings.baseline is not None) != ("baseline.sqlite" in names):
                    raise ValueError("Bundle baseline metadata/file mismatch")
                if settings.baseline:
                    raw = (root / "baseline.sqlite").read_bytes()
                    if hashlib.sha256(raw).hexdigest() != settings.baseline.digest:
                        raise ValueError("Bundle baseline checksum mismatch")
                    _database_valid(root / "baseline.sqlite")
        except (zipfile.BadZipFile, zlib.error, KeyError, RuntimeError, OSError, sqlite3.Error) as error:
            raise ValueError("Invalid or unreadable project bundle") from error
        yield root, bundle, settings


def import_project(data, directory, *, clone=False, name=None):
    directory = Path(directory).expanduser()
    if not directory.is_absolute() or directory.exists() or directory.is_symlink():
        raise ValueError("Import requires a new absolute project directory")
    directory = directory.parent.resolve() / directory.name
    if not directory.parent.is_dir():
        raise ValueError("Import destination parent must exist")
    with validated_bundle(data) as (root, bundle, settings):
        identity = uuid4().hex if clone else bundle.project.id
        project = Project(id=identity, name=name if name is not None else bundle.project.name,
                          active_version=bundle.project.active_version, directory=directory)
        registry = registry_directory()
        registry.mkdir(parents=True, exist_ok=True)
        entry = registry / f"{identity}.json"
        if entry.exists() or entry.is_symlink():
            raise ValueError("Project ID is already registered; use clone to assign a new ID")
        created = False
        registered = False
        try:
            directory.mkdir(mode=0o700)
            created = True
            (directory / "versions").mkdir()
            snapshot_code(root / "code", directory / "versions" / project.active_version)
            (directory / "drafts").mkdir()
            state = directory / ".reqlica"
            state.mkdir(mode=0o700)
            for source, target in ((root / "data.sqlite", directory / "data.sqlite"),):
                with target.open("xb") as stream:
                    os.chmod(target, 0o600)
                    stream.write(source.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
            scenario = ScenarioState(revision=uuid4().hex, rules=settings.scenarios.rules,
                remaining_failures={str(i): rule.failure_count for i, rule in enumerate(settings.scenarios.rules) if rule.failure_count is not None})
            atomic_write_json(state / "scenarios.json", scenario.model_dump(mode="json"))
            atomic_write_json(state / "mock-auth.json", settings.mock_auth.model_dump(mode="json"))
            atomic_write_json(state / "cors.json", settings.cors.model_dump())
            if settings.baseline:
                target = baseline_file(project, settings.baseline.revision)
                target.parent.mkdir()
                with target.open("xb") as stream:
                    os.chmod(target, 0o600)
                    stream.write((root / "baseline.sqlite").read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                _fsync_directory(target.parent)
                atomic_write_json(state / "data-baseline.json", settings.baseline.model_dump())
            atomic_write_json(state / "import.json", {"source_project_id":bundle.project.id, "environment_names":settings.environment_names})
            atomic_write_json(directory / "project.json", project.model_dump(mode="json", exclude={"directory"}))
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
            # Publish a fully prepared registration atomically without overwriting
            # an ID registered by another importer in the meantime.
            temporary = registry / f".import-{uuid4().hex}.json"
            try:
                atomic_write_json(temporary, {"directory":str(directory)})
                os.link(temporary, entry)
                registered = True
                _fsync_directory(registry)
            finally:
                temporary.unlink(missing_ok=True)
        except BaseException:
            if registered:
                entry.unlink(missing_ok=True)
            if created:
                shutil.rmtree(directory)
            raise
        return {"project":project.model_dump(mode="json"), "status":"imported", "cloned":clone,
                "environment_names_required":settings.environment_names, "source_revision":bundle.revision}
