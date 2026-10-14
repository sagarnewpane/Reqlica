"""Checked code snapshots, durable publication, and project serialization."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from reqlica.projects.store import Project, get_project

_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()
_lock_depth = threading.local()
_lock_descriptors: set[int] = set()
_LEGACY_EXCLUDES = {"project.json", "drafts", "versions", ".reqlica"}


def _reset_locks_after_fork() -> None:
    global _locks, _locks_guard, _lock_depth, _lock_descriptors
    # A child must neither inherit thread ownership nor keep the parent's flock alive.
    for descriptor in _lock_descriptors:
        os.close(descriptor)
    _locks = {}
    _locks_guard = threading.Lock()
    _lock_depth = threading.local()
    _lock_descriptors = set()


os.register_at_fork(
    before=lambda: _locks_guard.acquire(),
    after_in_parent=lambda: _locks_guard.release(),
    after_in_child=_reset_locks_after_fork,
)


def _checked_directory(directory: Path) -> Path:
    directory = Path(directory).absolute()
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError(f"Code directory must be a real directory: {directory}")
    # Canonicalize ancestors (including system temp-dir aliases), not the leaf.
    return directory.parent.resolve() / directory.name


def code_directory(project: Project) -> Path:
    """Return checked active code, or the root of an unconverted legacy project."""
    directory = _checked_directory(project.directory)
    if project.active_version is None:
        return directory
    if not re.fullmatch(r"[0-9a-f]{32}", project.active_version):
        raise ValueError("Invalid active version")
    versions = _checked_directory(directory / "versions")
    return _checked_directory(versions / project.active_version)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, payload: object) -> None:
    """Publish JSON with a durable, same-directory atomic replacement."""
    path = Path(path)
    serialized = (
        json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    )
    descriptor, filename = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(serialized)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def project_lock(directory: Path) -> Iterator[None]:
    """Serialize the same project across threads/processes, allowing nesting."""
    directory = _checked_directory(directory)
    with _locks_guard:
        lock = _locks.setdefault(directory, threading.RLock())
    with lock:
        depths = getattr(_lock_depth, "depths", None)
        if depths is None:
            depths = _lock_depth.depths = {}
        depth = depths.get(directory, 0)
        depths[directory] = depth + 1
        descriptor = None
        try:
            if not depth:
                state = directory / ".reqlica"
                state.mkdir(exist_ok=True)
                _checked_directory(state)
                # Persist the recovery directory's entry in its parent as well.
                _fsync_directory(directory)
                with _locks_guard:
                    descriptor = os.open(
                        state / "project.lock",
                        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                        0o600,
                    )
                    _lock_descriptors.add(descriptor)
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("Project lock must be a regular file")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            depths[directory] -= 1
            if not depths[directory]:
                del depths[directory]
            if descriptor is not None:
                with _locks_guard:
                    os.close(descriptor)
                    _lock_descriptors.discard(descriptor)


def _code_entries(directory: Path) -> Iterator[tuple[Path, bool]]:
    legacy = (directory / "project.json").exists()

    def walk(parent: Path) -> Iterator[tuple[Path, bool]]:
        for path in sorted(parent.iterdir(), key=lambda path: path.name):
            if (
                parent == directory
                and legacy
                and (
                    path.name in _LEGACY_EXCLUDES
                    or path.name == "data.sqlite"
                    or path.name.startswith("data.sqlite-")
                )
            ):
                continue
            mode = path.lstat().st_mode
            is_directory = stat.S_ISDIR(mode)
            if not is_directory and not stat.S_ISREG(mode):
                raise ValueError(
                    f"Code must contain only regular files/directories: {path}"
                )
            if path.name == "__pycache__" or (
                not is_directory and path.suffix == ".pyc"
            ):
                continue
            yield path.relative_to(directory), is_directory
            if is_directory:
                yield from walk(path)

    yield from walk(directory)


def _read_code_file(directory: Path, relative: Path) -> bytes:
    # Walk with directory descriptors so a swapped ancestor cannot escape the root.
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            os.close(parent)
            parent = child
        descriptor = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError(
                f"Code must contain only regular files: {directory / relative}"
            )
        return file.read()


def content_digest(directory: Path) -> str:
    """Hash sorted file paths and bytes, not timestamps or Python caches."""
    directory = _checked_directory(directory)
    digest = hashlib.sha256()
    for relative, is_directory in _code_entries(directory):
        if is_directory:
            continue
        name = relative.as_posix().encode("utf-8")
        content = _read_code_file(directory, relative)
        # Length framing avoids ambiguous path/content concatenations.
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def snapshot_code(source: Path, destination: Path) -> str:
    """Create a new verified snapshot; never modify an existing destination."""
    source = _checked_directory(source)
    destination = Path(destination).absolute()
    destination = _checked_directory(destination.parent) / destination.name
    if destination == source:
        raise ValueError("Snapshot destination must differ from source")
    if destination.is_relative_to(source):
        relative = destination.relative_to(source)
        if not (
            (source / "project.json").exists()
            and relative.parts[0] in {"versions", "drafts", ".reqlica"}
        ):
            raise ValueError("Snapshot destination cannot be inside source code")
    before = content_digest(source)
    created = False
    try:
        destination.mkdir()
        created = True
        directories = [destination]
        for relative, is_directory in _code_entries(source):
            path = destination / relative
            if is_directory:
                path.mkdir()
                directories.append(path)
            else:
                content = _read_code_file(source, relative)
                with path.open("xb") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
        if before != content_digest(source) or before != content_digest(destination):
            raise ValueError("Code changed while creating snapshot")
        for directory in reversed(directories):
            _fsync_directory(directory)
        _fsync_directory(destination.parent)
        return before
    except BaseException:
        if created:
            shutil.rmtree(destination)
        raise


def convert_project(project_id: str) -> Project:
    """Explicitly snapshot legacy code, retaining originals and publishing last.

    Callers must ensure the project's worker is stopped before conversion.
    """
    project = get_project(project_id)
    with project_lock(project.directory):
        project = get_project(project_id)
        if project.active_version is not None:
            code_directory(project)
            return project
        source = code_directory(project)
        versions = project.directory / "versions"
        versions.mkdir(exist_ok=True)
        _checked_directory(versions)
        drafts = project.directory / "drafts"
        drafts.mkdir(exist_ok=True)
        _checked_directory(drafts)
        database = project.directory / "data.sqlite"
        if database.exists() or database.is_symlink():
            if not stat.S_ISREG(database.lstat().st_mode):
                raise ValueError("Project database must be a regular file")
        else:
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA user_version = 0")
        version = uuid4().hex
        snapshot_code(source, versions / version)
        converted = project.model_copy(update={"active_version": version})
        atomic_write_json(
            project.directory / "project.json",
            converted.model_dump(mode="json", exclude={"directory"}),
        )
        return converted
