"""Load each project's handlers in its own supervised server process."""

import asyncio
import hashlib
import importlib.util
import multiprocessing
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


def build_app(directory: Path) -> FastAPI:
    """Build a worker app; this function is only called in the worker process."""
    directory = directory.resolve()
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
            module_name = (
                "_reqlica_handler_"
                + hashlib.sha256(str(handler_path).encode()).hexdigest()
            )
            module_spec = importlib.util.spec_from_file_location(
                module_name, handler_path
            )
            if module_spec is None or module_spec.loader is None:
                raise ValueError(f"Cannot load handler module: {endpoint.handler}")
            module = importlib.util.module_from_spec(module_spec)
            # FastAPI needs the module registered to resolve handler type annotations.
            sys.modules[module_name] = module
            module_spec.loader.exec_module(module)
            loaded_modules[handler_path] = module

        module = loaded_modules[handler_path]
        handler = getattr(module, function_name, None)
        if not callable(handler):
            raise TypeError(f"Handler is not callable: {endpoint.handler}")
        app.add_api_route(endpoint.path, handler, methods=[endpoint.method])
    app.state.manifest = manifest
    return app


def _run_worker(
    directory: Path,
    project_id: str,
    listener: socket.socket,
    startup_sender: Connection,
) -> None:
    """Run in the child process and tell the parent whether startup succeeded."""

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            # Report readiness only after Uvicorn is accepting connections.
            if self.started:
                startup_sender.send((True, app.state.manifest.model_dump(mode="json")))

    try:
        app = build_app(directory)
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


class ProjectStoppedError(RuntimeError):
    """An explicitly stopped project must not restart on a mock request."""


class WorkerManager:
    """Start workers on demand, reuse them, and own their cleanup."""

    def __init__(self, startup_timeout: float = 10.0):
        self.workers: dict[str, Worker] = {}
        self.startup_timeout = startup_timeout
        self._process_context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._closed = False
        self._stopped: dict[str, dict] = {}
        self._errors: dict[str, str] = {}

    @staticmethod
    def _stop_worker(worker: Worker) -> None:
        process = worker.process
        if process.pid is None:
            process.close()
            return
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()
        process.close()

    def get_or_start(self, project_id: str, *, explicit: bool = False) -> Worker:
        """Return the live worker, or start a replacement if it has exited."""
        with self._lock:
            return self._get_or_start(project_id, explicit=explicit)

    def _get_or_start(self, project_id: str, *, explicit: bool) -> Worker:
        if self._closed:
            raise RuntimeError("Worker manager is closed")
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
            worker = self._start_worker(project)
        except Exception as exc:
            self._errors[project_id] = str(exc)
            raise
        self.workers[project_id] = worker
        self._stopped.pop(project_id, None)
        self._errors.pop(project_id, None)
        return worker

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
            worker = self.workers.pop(project_id, None)
            if worker is not None:
                self._stop_worker(worker)
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
                        (project.directory / "manifest.json").read_text(
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
                args=(project.directory, project.id, listener, startup_sender),
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
                self._stop_worker(worker)
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
            self.workers.clear()
