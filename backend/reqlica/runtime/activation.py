"""Publish trusted code with bounded downtime and conservative crash recovery."""

import fcntl
import json
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from reqlica.projects.drafts import draft_directory, require_approved
from reqlica.projects.store import Project, get_project
from reqlica.projects.versions import (
    atomic_write_json,
    code_directory,
    content_digest,
    project_lock,
    snapshot_code,
)
from reqlica.runtime.sdk import backup_database, restore_database

DRAIN_TIMEOUT = 5.0


@contextmanager
def database_guard(directory: Path):
    """Exclude recovery from live workers, including children of a crashed gateway."""
    state = directory / ".reqlica"
    state.mkdir(exist_ok=True)
    if state.is_symlink():
        raise ValueError("Project state cannot be a symlink")
    descriptor = os.open(
        state / "runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("A project worker still owns the database")
                time.sleep(0.02)
        yield
    finally:
        os.close(descriptor)


def _cleanup(directory: Path) -> None:
    state = directory / ".reqlica"
    # Remove the journal durably before its recovery dependency.
    (state / "activation.json").unlink(missing_ok=True)
    descriptor = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    (state / "activation-backup.sqlite").unlink(missing_ok=True)


def recover_activation(project: Project) -> None:
    """Under project_lock, finish a commit or roll an interrupted attempt back."""
    journal = project.directory / ".reqlica" / "activation.json"
    if not journal.exists():
        return
    record = json.loads(journal.read_text(encoding="utf-8"))
    previous = Project.model_validate(
        {**record["previous"], "directory": project.directory}
    )
    if previous.id != project.id or record["phase"] not in {
        "prepared",
        "backed_up",
        "committed",
        "recovered",
    }:
        raise ValueError("Invalid activation recovery journal")
    if record["phase"] in {"committed", "recovered"}:
        _cleanup(project.directory)
        return
    if record["phase"] == "backed_up":
        with database_guard(project.directory):
            restore_database(
                project.directory / ".reqlica" / "activation-backup.sqlite",
                project.directory / "data.sqlite",
            )
    # A prepared attempt never executed candidate code or changed the active pointer.
    atomic_write_json(project.directory / "project.json", record["previous"])
    atomic_write_json(journal, {**record, "phase": "recovered"})
    _cleanup(project.directory)


def _schema(database: Path) -> list:
    with closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        return connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()


def activate_draft(
    manager, project_id: str, draft_id: str, expected_revision: str
) -> dict:
    from reqlica.runtime.worker import ProjectActivatingError

    project = get_project(project_id)
    with manager._lock:
        if manager._closed:
            raise RuntimeError("Worker manager is closed")
        if project_id in manager._activating:
            raise ProjectActivatingError("Project is already activating")
        manager._activating.add(project_id)
    candidate = None
    stopped_old = False
    was_running = False
    recovery_blocked = False
    journal_started = False
    try:
        with project_lock(project.directory):
            recover_activation(project)
            project = get_project(project_id)
            require_approved(project_id, draft_id, expected_revision)
            version = uuid4().hex
            revision = snapshot_code(
                draft_directory(project, draft_id),
                project.directory / "versions" / version,
            )
            if revision != expected_revision:
                raise ValueError("Approved draft changed while publishing")
            next_project = project.model_copy(update={"active_version": version})
            with manager._condition:
                deadline = time.monotonic() + DRAIN_TIMEOUT
                while manager._requests.get(project_id, 0):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Activation timed out draining requests; old version retained"
                        )
                    manager._condition.wait(remaining)
                old = manager.workers.get(project_id)
                was_running = old is not None and old.process.is_alive()
            journal = project.directory / ".reqlica" / "activation.json"
            record = {
                "phase": "prepared",
                "previous": project.model_dump(mode="json", exclude={"directory"}),
                "candidate": version,
                "revision": revision,
            }
            # Journal before stopping or allowing candidate code to touch live data.
            journal_started = True
            atomic_write_json(journal, record)
            with manager._lock:
                if manager._closed:
                    raise RuntimeError("Worker manager closed during activation")
                # close() must not see a registered process with a closed handle.
                old = manager.workers.get(project_id)
                if old is not None:
                    manager._stop_worker(old)
                stopped_old = True
                manager.workers.pop(project_id, None)
            backup = project.directory / ".reqlica" / "activation-backup.sqlite"
            with database_guard(project.directory):
                backup_database(project.directory / "data.sqlite", backup)
                with backup.open("rb") as file:
                    os.fsync(file.fileno())
                record["phase"] = "backed_up"
                atomic_write_json(journal, record)
            candidate = manager._start_worker(next_project)
            if _schema(project.directory / "data.sqlite") != _schema(backup):
                raise ValueError("Schema-changing activation is not supported")
            if not was_running:
                manager._stop_worker(candidate)
                candidate = None
            published = code_directory(next_project)
            if (published / "project.json").exists() or content_digest(
                published
            ) != revision:
                raise ValueError("Published code changed during candidate startup")
            with manager._lock:
                if manager._closed:
                    raise RuntimeError("Worker manager closed during activation")
                if candidate is not None and not candidate.process.is_alive():
                    raise RuntimeError("Candidate worker exited during activation")
                from reqlica.projects.schemas import promote_draft_schema

                promote_draft_schema(project, draft_id, version, revision)
                atomic_write_json(
                    project.directory / "project.json",
                    next_project.model_dump(mode="json", exclude={"directory"}),
                )
                atomic_write_json(journal, {**record, "phase": "committed"})
                if candidate is not None:
                    manager.workers[project_id] = candidate
                manager._errors.pop(project_id, None)
                result = manager._describe_project(project_id)
                if project_id in manager._stopped:
                    manager._stopped[project_id] = result
            try:
                _cleanup(project.directory)
            except OSError:
                # The durable commit is authoritative; retry cleanup on startup.
                pass
            return result
    except BaseException as error:
        if journal_started:
            with project_lock(project.directory):
                try:
                    with manager._lock:
                        if stopped_old and candidate is None:
                            # Failed startup can retain a child whose cleanup failed.
                            candidate = manager.workers.get(project_id)
                        if candidate is not None:
                            # Retain even unready children until shutdown is confirmed.
                            if not candidate.closed:
                                manager.workers[project_id] = candidate
                            manager._stop_worker(candidate)
                            manager.workers.pop(project_id, None)
                    recover_activation(project)
                    with manager._lock:
                        if stopped_old and was_running and not manager._closed:
                            restored = manager._start_worker(get_project(project_id))
                            manager.workers[project_id] = restored
                except Exception as recovery_error:
                    recovery_blocked = True
                    with manager._lock:
                        manager._errors[project_id] = (
                            f"Activation recovery failed: {recovery_error}"
                        )
                    raise RuntimeError(
                        f"Activation failed ({error}); recovery failed ({recovery_error})"
                    ) from recovery_error
        raise
    finally:
        with manager._condition:
            if not recovery_blocked:
                manager._activating.discard(project_id)
            manager._condition.notify_all()
