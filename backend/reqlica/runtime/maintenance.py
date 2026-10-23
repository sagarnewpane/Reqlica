"""Fence traffic and stop workers for explicit maintenance operations."""

import time
from contextlib import contextmanager
from reqlica.projects.store import get_project
from reqlica.projects.versions import project_lock
from reqlica.runtime.activation import DRAIN_TIMEOUT, database_guard, recover_activation


@contextmanager
def maintenance(manager, project_id, precheck=None):
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
            if precheck is not None:
                precheck(project)
            with manager._condition:
                deadline = time.monotonic() + DRAIN_TIMEOUT
                while manager._requests.get(project_id, 0):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Maintenance timed out draining requests")
                    manager._condition.wait(remaining)
                if manager._closed:
                    raise RuntimeError("Worker manager closed during maintenance")
                worker = manager.workers.get(project_id)
                if worker:
                    manager._stop_worker(worker)
                    manager.workers.pop(project_id, None)
                manager._stopped[project_id] = {"id":project.id, "name":project.name,
                    "directory":str(project.directory), "status":"stopped", "pid":None,
                    "endpoints":worker.manifest.model_dump()["endpoints"] if worker and worker.manifest else [], "error":None}
            with database_guard(project.directory):
                yield project
    finally:
        with manager._condition:
            manager._activating.discard(project_id)
            manager._condition.notify_all()
