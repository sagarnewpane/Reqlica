"""Confirmed maintenance endpoints owned by the gateway's worker manager."""

import sqlite3
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field
from typing import Literal
from reqlica.projects.reset import ConfirmedRevision
from reqlica.projects.migrations import MigrationApply, MigrationRollback, apply_migration, rollback_migration, check_migration, check_rollback, recover_migration, check_recovery
from reqlica.projects.workspace import export_workspace
from reqlica.runtime.maintenance import maintenance
from reqlica.runtime.relocation import Relocation, relocate
from reqlica.runtime.worker import ProjectActivatingError

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Maintenance"])


class WorkspaceExport(ConfirmedRevision):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    include_secrets: bool = Field(default=False, strict=True)


class WorkerControl(ConfirmedRevision):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["start", "stop", "restart"]


def _control_worker(manager, project_id, body):
    from reqlica.runtime.activation import recover_activation
    from reqlica.projects.store import get_project
    from reqlica.projects.versions import code_directory, content_digest, project_lock
    # Match the worker manager's lock order; check and control one published version.
    with manager._lock:
        if project_id in manager._activating:
            raise ProjectActivatingError("Project is activating; inspect it again shortly")
        project = get_project(project_id)
        with project_lock(project.directory):
            recover_activation(project)
            project = get_project(project_id)
            if content_digest(code_directory(project)) != body.expected_revision:
                raise ValueError("Project changed; inspect it again before worker control")
            if body.action in {"stop", "restart"}:
                result = manager.stop(project_id)
            if body.action in {"start", "restart"}:
                result = manager.start(project_id)
            return result


@router.post("/worker-control")
def control_worker(request: Request, project_id: str, body: WorkerControl):
    return operation(_control_worker, request.app.state.worker_manager, project_id, body)


def operation(function, *args):
    try:
        return function(*args)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except (OSError, RuntimeError, ProjectActivatingError, sqlite3.Error) as error:
        raise HTTPException(503, str(error)) from error


@router.post("/relocate")
def move(request: Request, project_id: str, body: Relocation):
    return operation(relocate, request.app.state.worker_manager, project_id, body)


def _archive(manager, project_id, body):
    from reqlica.projects.versions import code_directory, content_digest
    from reqlica.projects.scenarios import _scenario_lock
    def check(project):
        if content_digest(code_directory(project)) != body.expected_revision:
            raise ValueError("Project changed; inspect it again before archival")
    with maintenance(manager, project_id, check) as project:
        with _scenario_lock(project.directory):
            return export_workspace(project, body.include_secrets)


@router.post("/workspace-export")
def archive(request: Request, project_id: str, body: WorkspaceExport):
    data = operation(_archive, request.app.state.worker_manager, project_id, body)
    return Response(data, media_type="application/zip", headers={"Cache-Control":"no-store", "Content-Disposition":f'attachment; filename="workspace-{project_id}.zip"'})


def _migrate(manager, project_id, body, rollback=False):
    precheck = check_rollback if rollback else check_migration
    with maintenance(manager, project_id, lambda project: precheck(project, body)) as project:
        return rollback_migration(project, body) if rollback else apply_migration(project, body)


@router.post("/database-migrations/apply")
def migrate(request: Request, project_id: str, body: MigrationApply):
    return operation(_migrate, request.app.state.worker_manager, project_id, body)


@router.post("/database-migrations/rollback")
def rollback(request: Request, project_id: str, body: MigrationRollback):
    return operation(_migrate, request.app.state.worker_manager, project_id, body, True)


@router.post("/database-migrations/recover")
def recover(request: Request, project_id: str, body: MigrationRollback):
    def perform():
        with maintenance(request.app.state.worker_manager, project_id, lambda project: check_recovery(project, body)) as project:
            return recover_migration(project, body)
    return operation(perform)
