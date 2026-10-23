"""Drain and stop a project's worker before replacing live SQLite contents."""

import time

from reqlica.projects.reset import checked_baseline, restore_baseline
from reqlica.projects.store import get_project
from reqlica.projects.versions import project_lock
from reqlica.runtime.activation import DRAIN_TIMEOUT, database_guard, recover_activation


def reset_data(manager, project_id, body):
    from reqlica.runtime.worker import ProjectActivatingError

    project = get_project(project_id)
    with manager._condition:
        if manager._closed:
            raise RuntimeError("Worker manager is closed")
        if project_id in manager._activating:
            raise ProjectActivatingError("Project is changing; retry shortly")
        manager._activating.add(project_id)
    try:
        with project_lock(project.directory):
            recover_activation(project)
            project = get_project(project_id)
            checked_baseline(project, body)
            with manager._condition:
                deadline = time.monotonic() + DRAIN_TIMEOUT
                while manager._requests.get(project_id, 0):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Data reset timed out draining requests; data retained")
                    manager._condition.wait(remaining)
                if manager._closed:
                    raise RuntimeError("Worker manager closed during reset")
                worker = manager.workers.get(project_id)
                if worker is not None:
                    manager._stop_worker(worker)
                    manager.workers.pop(project_id, None)
                # Leave stopped even on restore failure; no import-time code runs
                # against a reset database until the user explicitly starts it.
                manager._stopped[project_id] = {
                    "id": project.id, "name": project.name,
                    "directory": str(project.directory), "status": "stopped", "pid": None,
                    "endpoints": worker.manifest.model_dump()["endpoints"] if worker is not None and worker.manifest else [],
                    "error": None,
                }
            with database_guard(project.directory):
                result = restore_baseline(project, body)
            with manager._condition:
                manager._errors.pop(project_id, None)
            return result
    finally:
        with manager._condition:
            manager._activating.discard(project_id)
            manager._condition.notify_all()
