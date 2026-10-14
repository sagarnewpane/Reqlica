"""Load each project's handlers in its own supervised server process."""

import asyncio
import hashlib
import importlib
import importlib.util
import multiprocessing
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from types import ModuleType

import uvicorn
from fastapi import FastAPI
from reqlica.projects.store import Manifest, Project, get_project, list_project_ids
from reqlica.projects.versions import code_directory, project_lock
from reqlica.runtime.sdk import configure_database


def build_app(directory: Path, database: Path | None = None) -> FastAPI:
    """Build a worker app; this function is only called in the worker process."""
    directory = directory.resolve()
    configure_database(database if database is not None else directory / "data.sqlite")
    manifest_file = directory / "manifest.json"
    manifest = Manifest.model_validate_json(manifest_file.read_text())
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sys.path.insert(0, str(directory))
    loaded_modules: dict[Path, ModuleType] = {}

    for endpoint in manifest.endpoints:
        filename, function_name = endpoint.handler.split(":")
        handler_path = (directory / filename).resolve()
        if (
            not handler_path.is_relative_to(directory)
            or handler_path.suffix != ".py"
            or not handler_path.is_file()
        ):
            raise ValueError(
                f"Handler must be a regular .py file inside the project: {endpoint.handler}"
            )
        if handler_path not in loaded_modules:
            relative = handler_path.relative_to(directory).with_suffix("")
            parts = list(relative.parts)
            if parts[-1] == "__init__":
                parts.pop()
            if parts and all(part.isidentifier() for part in parts):
                # Tests and supporting imports must reuse the registered handler,
                # including its globals and any import-time side effects.
                module = importlib.import_module(".".join(parts))
                if Path(module.__file__ or "").resolve() != handler_path:
                    raise ValueError(
                        f"Handler module conflicts with an installed module: {filename}"
                    )
                loaded_modules[handler_path] = module
            else:
                loaded_modules[handler_path] = _load_handler(handler_path)

        module = loaded_modules[handler_path]
        handler = getattr(module, function_name, None)
        if not callable(handler):
            raise TypeError(f"Handler is not callable: {endpoint.handler}")
        app.add_api_route(endpoint.path, handler, methods=[endpoint.method])
    app.state.manifest = manifest
    app.state.database = database
    return app


def _load_handler(handler_path: Path) -> ModuleType:
    """Support filenames that cannot be addressed by an ordinary Python import."""
    module_name = (
        "_reqlica_handler_" + hashlib.sha256(str(handler_path).encode()).hexdigest()
    )
    module_spec = importlib.util.spec_from_file_location(module_name, handler_path)
    if module_spec is None or module_spec.loader is None:
        raise ValueError(f"Cannot load handler module: {handler_path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module


def _run_worker(
    directory: Path,
    project_id: str,
    listener: socket.socket,
    startup_sender: Connection,
    database: Path,
    parent_pid: int,
) -> None:
    """Run in the child process and tell the parent whether startup succeeded."""

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            # Report readiness only after Uvicorn is accepting connections.
            if self.started:
                startup_sender.send((True, app.state.manifest.model_dump(mode="json")))

    def watch_parent():
        # A hard gateway crash must not leave a writable orphan project worker.
        while os.getppid() == parent_pid:
            time.sleep(0.1)
        os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        from reqlica.runtime.activation import database_guard

        with database_guard(database.parent):
            app = build_app(directory, database)
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                root_path=f"/mock/{project_id}",
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1",
                log_level="warning",
                access_log=False,
            )
            asyncio.run(ReadyServer(config).serve(sockets=[listener]))
    except (Exception, SystemExit, KeyboardInterrupt) as exc:  # noqa: BLE001
        # Handler code can fail arbitrarily; report startup errors to the gateway.
        try:
            startup_sender.send((False, f"{type(exc).__name__}: {exc}"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        startup_sender.close()
        listener.close()


@dataclass
class Worker:
    process: multiprocessing.Process
    port: int
    project: Project
    manifest: Manifest | None = None
    closed: bool = False


class ProjectStoppedError(RuntimeError):
    """An explicitly stopped project must not restart on a mock request."""


class ProjectActivatingError(RuntimeError):
    """Requests cannot enter a project during publication or conversion."""


class WorkerManager:
    """Start workers on demand, reuse them, and own their cleanup."""

    def __init__(self, startup_timeout: float = 10.0):
        self.workers: dict[str, Worker] = {}
        self.startup_timeout = startup_timeout
        self._process_context = multiprocessing.get_context("spawn")
        self._lock = threading.RLock()
        self._closed = False
        self._stopped: dict[str, dict] = {}
        self._errors: dict[str, str] = {}
        self._activating: set[str] = set()
        self._requests: dict[str, int] = {}
        self._condition = threading.Condition(self._lock)

    @staticmethod
    def _stop_worker(worker: Worker) -> None:
        if worker.closed:
            return
        process = worker.process
        if process.pid is None:
            process.close()
            worker.closed = True
            return
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()
        process.close()
        worker.closed = True

    def get_or_start(self, project_id: str, *, explicit: bool = False) -> Worker:
        """Return the live worker, or start a replacement if it has exited."""
        with self._lock:
            return self._get_or_start(project_id, explicit=explicit)

    def _get_or_start(self, project_id: str, *, explicit: bool) -> Worker:
        if self._closed:
            raise RuntimeError("Worker manager is closed")
        if project_id in self._activating:
            raise ProjectActivatingError("Project is activating; retry shortly")
        if project_id in self._stopped and not explicit:
            raise ProjectStoppedError(
                f"Project {project_id} is stopped; use 'reqlica project start {project_id}'"
            )

        worker = self.workers.get(project_id)
        if worker is not None:
            if worker.process.is_alive():
                return worker
            self.workers.pop(project_id)
            self._stop_worker(worker)

        project = get_project(project_id)
        try:
            from reqlica.runtime.activation import recover_activation

            with project_lock(project.directory):
                recover_activation(project)
                project = get_project(project_id)
                worker = self._start_worker(project)
        except Exception as exc:
            self._errors[project_id] = str(exc)
            raise
        self.workers[project_id] = worker
        self._stopped.pop(project_id, None)
        self._errors.pop(project_id, None)
        return worker

    def acquire_request(self, project_id: str) -> Worker:
        """Lease a worker until the proxy has consumed its response."""
        with self._lock:
            worker = self._get_or_start(project_id, explicit=False)
            self._requests[project_id] = self._requests.get(project_id, 0) + 1
            return worker

    def release_request(self, project_id: str) -> None:
        with self._condition:
            self._requests[project_id] -= 1
            if not self._requests[project_id]:
                del self._requests[project_id]
            self._condition.notify_all()

    def activate(self, project_id: str, draft_id: str, expected_revision: str) -> dict:
        from reqlica.runtime.activation import activate_draft

        return activate_draft(self, project_id, draft_id, expected_revision)

    def convert(self, project_id: str) -> dict:
        from reqlica.projects.versions import convert_project
        from reqlica.runtime.activation import database_guard

        with self._lock:
            if self._closed:
                raise RuntimeError("Worker manager is closed")
            if project_id in self._activating:
                raise ProjectActivatingError("Project is activating")
            worker = self.workers.get(project_id)
            if worker is not None and worker.process.is_alive():
                raise ValueError("Stop the project before converting it")
            project = get_project(project_id)
            with project_lock(project.directory), database_guard(project.directory):
                convert_project(project_id)
            result = self._describe_project(project_id)
            if project_id in self._stopped:
                self._stopped[project_id] = result
            return result

    def start(self, project_id: str) -> dict:
        """Explicitly start a worker and report its state under the same lock."""
        with self._lock:
            self._get_or_start(project_id, explicit=True)
            project = self._describe_project(project_id)
            if project["status"] != "running":
                raise RuntimeError(
                    project["error"] or "Project worker exited during startup"
                )
            return project

    def stop(self, project_id: str) -> dict:
        """Stop only this worker and disable lazy startup for this session."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Worker manager is closed")
            if project_id in self._activating:
                raise ProjectActivatingError("Project is activating; retry shortly")
            if project_id in self._stopped:
                return self._stopped[project_id]
            known_worker = self.workers.get(project_id)
            try:
                project = self._describe_project(project_id)
            except (OSError, ValueError) as exc:
                if known_worker is None and project_id not in list_project_ids():
                    raise
                project = self._unavailable_project(project_id, exc)
                if known_worker is not None:
                    project.update(
                        name=known_worker.project.name,
                        directory=str(known_worker.project.directory),
                        endpoints=(
                            known_worker.manifest.model_dump(mode="json")["endpoints"]
                            if known_worker.manifest is not None
                            else []
                        ),
                    )
            worker = self.workers.get(project_id)
            if worker is not None:
                self._stop_worker(worker)
                self.workers.pop(project_id, None)
            project = {**project, "status": "stopped", "pid": None}
            self._stopped[project_id] = project
            self._errors.pop(project_id, None)
            return project

    def describe_project(self, project_id: str) -> dict:
        with self._lock:
            return self._describe_project(project_id)

    def list_projects(self) -> list[dict]:
        """Report live process state without starting any workers."""
        with self._lock:
            project_ids = (
                set(list_project_ids()) | self.workers.keys() | self._stopped.keys()
            )
            projects = []
            for project_id in sorted(project_ids):
                try:
                    projects.append(self._describe_project(project_id))
                except (OSError, ValueError) as exc:
                    projects.append(self._unavailable_project(project_id, exc))
            return projects

    @staticmethod
    def _unavailable_project(project_id: str, error: Exception) -> dict:
        return {
            "id": project_id,
            "name": None,
            "directory": None,
            "status": "error",
            "pid": None,
            "endpoints": [],
            "error": str(error),
        }

    def _describe_project(self, project_id: str) -> dict:
        worker = self.workers.get(project_id)
        if worker is not None and not worker.process.is_alive():
            self.workers.pop(project_id)
            self._stop_worker(worker)
            self._errors[project_id] = "Project worker exited; start it again to retry"
            worker = None
        try:
            project = worker.project if worker is not None else get_project(project_id)
            # A running worker's routes may differ from a manifest edited on disk.
            if worker is not None:
                manifest = worker.manifest
            else:
                try:
                    manifest = Manifest.model_validate_json(
                        (code_directory(project) / "manifest.json").read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, ValueError) as exc:
                    raise ValueError(
                        f"Cannot read project {project_id} manifest: {exc}"
                    ) from exc
        except (OSError, ValueError) as exc:
            if project_id in self._stopped:
                return {**self._stopped[project_id], "error": str(exc)}
            raise
        if manifest is None:
            raise RuntimeError("Worker has not reported its loaded manifest")
        error = self._errors.get(project_id)
        status = "idle"
        if worker is not None:
            status = "running"
        elif project_id in self._stopped:
            status = "stopped"
        elif error is not None:
            status = "error"
        return {
            "id": project.id,
            "name": project.name,
            "directory": str(project.directory),
            "status": status,
            "pid": worker.process.pid if worker is not None else None,
            "endpoints": manifest.model_dump(mode="json")["endpoints"],
            "error": error,
            "active_version": project.active_version,
        }

    def _start_worker(self, project: Project) -> Worker:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            # Reserve the port before spawning, so another process cannot take it.
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            startup_receiver, startup_sender = self._process_context.Pipe(duplex=False)
            process = self._process_context.Process(
                target=_run_worker,
                args=(
                    code_directory(project),
                    project.id,
                    listener,
                    startup_sender,
                    project.directory / "data.sqlite",
                    os.getpid(),
                ),
                daemon=True,
            )
            worker = Worker(process, port, project)
            try:
                process.start()
                startup_sender.close()
                if not startup_receiver.poll(self.startup_timeout):
                    raise RuntimeError(
                        f"Worker startup timed out for project {project.id}"
                    )
                try:
                    started, startup_detail = startup_receiver.recv()
                except EOFError as exc:
                    raise RuntimeError(
                        f"Worker exited during startup for project {project.id}"
                    ) from exc
                if not started:
                    raise RuntimeError(
                        f"Worker startup failed for project {project.id}: {startup_detail}"
                    )
                if not process.is_alive():
                    raise RuntimeError(
                        f"Worker exited during startup for project {project.id}"
                    )
                worker.manifest = Manifest.model_validate(startup_detail)
            except BaseException:
                try:
                    self._stop_worker(worker)
                except Exception as cleanup_error:
                    with self._lock:
                        self.workers[project.id] = worker
                        self._activating.add(project.id)
                        self._errors[project.id] = (
                            f"Worker cleanup failed: {cleanup_error}; restart the gateway"
                        )
                    raise
                raise
            finally:
                startup_sender.close()
                startup_receiver.close()
        return worker

    def close(self) -> None:
        """Stop all workers with one shared two-second grace period."""
        with self._lock:
            self._closed = True
            processes = [worker.process for worker in self.workers.values()]

            # Signal every worker first, then share one bounded shutdown deadline.
            for process in processes:
                if process.is_alive():
                    process.terminate()

            deadline = time.monotonic() + 2
            for process in processes:
                remaining_time = max(0, deadline - time.monotonic())
                process.join(timeout=remaining_time)

            for process in processes:
                if process.is_alive():
                    process.kill()
            for process in processes:
                process.join()
                process.close()
            for worker in self.workers.values():
                worker.closed = True
            self.workers.clear()
