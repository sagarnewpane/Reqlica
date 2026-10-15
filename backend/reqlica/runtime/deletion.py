"""Remove a project through its worker owner so no stopped/error caches remain."""

import time

from reqlica.projects.deletion import (
    delete_project_registration,
    inspect_project_deletion,
)
from reqlica.runtime.activation import DRAIN_TIMEOUT


def deletion_info(manager, project_id: str, delete_files: bool = False) -> dict:
    with manager._lock:
        known = (
            project_id in manager.workers
            or project_id in manager._stopped
            or project_id in manager._errors
        )
        return inspect_project_deletion(project_id, delete_files, allow_missing=known)


def delete_project(
    manager,
    project_id: str,
    delete_files: bool = False,
    expected_directory: str | None = None,
) -> dict:
    from reqlica.runtime.worker import ProjectActivatingError

    with manager._condition:
        if manager._closed:
            raise RuntimeError("Worker manager is closed")
        if project_id in manager._activating:
            raise ProjectActivatingError("Project is changing; retry deletion shortly")
        # Inspect and fence atomically against other gateway lifecycle operations.
        # Unsafe targets are rejected before stopping a healthy worker.
        info = deletion_info(manager, project_id, delete_files)
        if delete_files and expected_directory != info["directory"]:
            raise ValueError(
                "Project directory changed; inspect and confirm deletion again"
            )
        manager._activating.add(project_id)
    stopped = False
    try:
        with manager._condition:
            deadline = time.monotonic() + DRAIN_TIMEOUT
            while manager._requests.get(project_id, 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Deletion timed out draining requests; project retained"
                    )
                manager._condition.wait(remaining)
            if manager._closed:
                raise RuntimeError("Worker manager closed during deletion")
            worker = manager.workers.get(project_id)
            if worker is not None:
                manager._stop_worker(worker)
                manager.workers.pop(project_id, None)
            stopped = True
        result = delete_project_registration(
            project_id,
            delete_files,
            expected_directory,
            allow_missing=True,
        )
        with manager._condition:
            manager._stopped.pop(project_id, None)
            manager._errors.pop(project_id, None)
            manager._requests.pop(project_id, None)
        return result
    except Exception as error:
        if stopped:
            # If disk cleanup failed, keep it stopped rather than lazily restarting
            # potentially partially deleted code. Keep the registration for diagnosis.
            with manager._condition:
                failure = f"Deletion failed: {error}"
                manager._errors[project_id] = failure
                manager._stopped[project_id] = {
                    "id": project_id,
                    "name": info["name"],
                    "directory": info["directory"],
                    "status": "stopped",
                    "pid": None,
                    "endpoints": [],
                    "error": failure,
                }
        raise
    finally:
        with manager._condition:
            manager._activating.discard(project_id)
            manager._condition.notify_all()
