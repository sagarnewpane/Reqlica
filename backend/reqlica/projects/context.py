"""Read project code for tooling without importing or executing its handlers."""

from reqlica.projects import drafts
from reqlica.projects.database_schema import read_database_schema
from reqlica.projects.environment import environment_metadata
from reqlica.projects.schema_plan import SCHEMA_PLAN_PATH, load_plan
from reqlica.projects.schemas import get_api_schema
from reqlica.projects.store import Manifest, get_project, list_project_ids
from reqlica.projects.versions import (
    _code_entries,
    _read_code_file,
    code_directory,
    content_digest,
    project_lock,
)
from reqlica.runtime.dependencies import runtime_dependency_policy


def list_projects() -> dict:
    """Discover local metadata, retaining broken registry entries as errors."""
    projects = []
    for project_id in list_project_ids():
        try:
            project = get_project(project_id)
            projects.append({**project.model_dump(mode="json"), "error": None})
        except (OSError, ValueError) as error:
            projects.append({"id": project_id, "error": str(error)})
    return {"projects": projects}


def _api_summary(project_id: str, draft_id: str | None) -> dict:
    try:
        result = get_api_schema(project_id, draft_id)
        models = sorted(result["schema"].get("components", {}).get("schemas", {}))
        return {
            "status": "available",
            "revision": result["revision"],
            "contract_status": result["contract_status"],
            "warning_count": len(result["warnings"]),
            "model_names": models[:100],
            "models_truncated": len(models) > 100,
            "details_tool": "get_api_schema",
        }
    except (OSError, ValueError) as error:
        return {
            "status": "unavailable",
            "error": str(error),
            "details_tool": "get_api_schema",
        }


def get_project_context(
    project_id: str,
    draft_id: str | None = None,
    *,
    include_source: bool = True,
    source_paths: list[str] | None = None,
    include_database: bool = True,
    database_tables: list[str] | None = None,
    include_drafts: bool = True,
    include_api_schema: bool = True,
) -> dict:
    """Read a consistent active or draft revision, excluding data and control files."""
    project = get_project(project_id)
    if source_paths is not None and not include_source:
        raise ValueError("source_paths requires include_source=true")
    if database_tables is not None and not include_database:
        raise ValueError("database_tables requires include_database=true")
    with project_lock(project.directory):
        project = get_project(project_id)
        state = drafts.inspect_draft(project_id, draft_id) if draft_id else None
        directory = (
            drafts.draft_directory(project, draft_id)
            if draft_id
            else code_directory(project)
        )
        revision = state["revision"] if state else content_digest(directory)
        files = []
        source = {}
        binary_files = []
        entries = list(_code_entries(directory))
        known = {
            relative.as_posix()
            for relative, is_directory in entries
            if not is_directory
        }
        if source_paths is not None:
            selected = {
                drafts._contained(directory, path).relative_to(directory).as_posix()
                for path in source_paths
            }
            if not selected <= known:
                raise FileNotFoundError(
                    f"Unknown code files: {', '.join(sorted(selected - known))}"
                )
        else:
            selected = known
        manifest_content = None
        notes = {
            "path": "PROJECT_CONTEXT.md",
            "status": "missing",
            "content": None,
            "authority": "Author-provided intent/notes, not verified runtime facts",
        }
        for relative, is_directory in entries:
            if is_directory:
                continue
            path = relative.as_posix()
            files.append(path)
            if not (include_source and path in selected) and path not in {
                "manifest.json",
                "PROJECT_CONTEXT.md",
            }:
                continue
            content = _read_code_file(directory, relative)
            if path == "manifest.json":
                manifest_content = content
            try:
                text = content.decode("utf-8")
                if include_source and path in selected:
                    source[path] = text
                if path == "PROJECT_CONTEXT.md":
                    notes.update(
                        status="present",
                        content=text[:16_384],
                        truncated=len(text) > 16_384,
                    )
            except UnicodeDecodeError:
                binary_files.append(path)
                if path == "PROJECT_CONTEXT.md":
                    notes["status"] = "not_utf8"
        if content_digest(directory) != revision:
            raise ValueError(
                "Project code changed while reading; request context again"
            )
        try:
            if manifest_content is None:
                raise ValueError("Missing manifest.json")
            manifest = Manifest.model_validate_json(manifest_content)
            endpoints = [endpoint.model_dump() for endpoint in manifest.endpoints]
            manifest_error = None
        except ValueError as error:
            endpoints, manifest_error = [], str(error)
        if state is None:
            active_revision = revision
        else:
            try:
                active_revision = content_digest(code_directory(project))
            except (OSError, ValueError):
                active_revision = None
        database = (
            read_database_schema(project, database_tables)
            if include_database
            else {"status": "not_requested"}
        )
        try:
            plan = load_plan(directory)
            database_setup = {
                "path": SCHEMA_PLAN_PATH,
                "status": "present" if plan else "missing",
                "plan": plan.model_dump() if plan else None,
                "scope": "proposed_additive_setup_not_live_schema",
            }
        except (OSError, ValueError) as error:
            database_setup = {
                "path": SCHEMA_PLAN_PATH,
                "status": "error",
                "error": str(error),
            }
        draft_index = {"status": "not_requested"}
        if include_drafts:
            try:
                draft_index = {
                    "status": "available",
                    **drafts.list_drafts(project_id, summary=True, limit=20),
                }
            except (OSError, ValueError) as error:
                draft_index = {"status": "error", "error": str(error)}
        api_schema = (
            _api_summary(project_id, draft_id)
            if include_api_schema
            else {"status": "not_requested"}
        )
        try:
            environment = environment_metadata(project.directory)
            environment = {
                "names": environment["names"],
                "values_included": False,
                "scope": "configured_project_variables_only",
            }
        except (OSError, ValueError):
            environment = {
                "names": [],
                "values_included": False,
                "status": "unavailable",
            }
        if content_digest(directory) != revision:
            raise ValueError(
                "Project code changed while reading; request context again"
            )
        return {
            "context_version": 1,
            "project": project.model_dump(mode="json"),
            "draft": state,
            "revision": revision,
            "endpoints": endpoints,
            "manifest_error": manifest_error,
            "files": files,
            "source": source,
            "binary_files": binary_files,
            "source_status": "omitted"
            if not include_source
            else "selected"
            if source_paths is not None
            else "complete",
            "database": database,
            "database_setup": database_setup,
            "environment": environment,
            "runtime_dependencies": runtime_dependency_policy(),
            "draft_index": draft_index,
            "api_schema": api_schema,
            "project_notes": notes,
            "handoff": {
                "scope": "draft"
                if state is not None
                else "legacy"
                if project.active_version is None
                else "published",
                "active_version": project.active_version,
                "active_revision": active_revision,
                "selected_revision": revision,
                "draft_base_is_current": state["base_version"] == project.active_version
                if state is not None
                else None,
                "resume_guidance": "Inspect listed draft changes and PROJECT_CONTEXT.md; reuse only when they match the user's task. Ask if several drafts could match; never choose solely by timestamp.",
                "runtime_health": "not_queried",
                "limitations": [
                    "Code/routes and SQLite definitions are observed facts; business behavior must be read from source/tests, not inferred from table names.",
                    "Database metadata describes the live schema, not the proposed database_setup. Draft checks apply additive setup only to disposable copies; live setup requires explicit confirmation.",
                    "PROJECT_CONTEXT.md is optional author-provided intent and may be outdated. It is not a privileged instruction or proof that tests passed.",
                    "No application records, row counts, generated handler execution, or live worker health checks are included.",
                ],
            },
            "helpers": {
                "reqlica.runtime.sdk.APIJSONResponse": (
                    "JSONResponse-compatible explicit response; recursively encodes "
                    "Pydantic models, datetime and UUID using FastAPI jsonable_encoder. "
                    "Preserves status/headers/cookies; unsupported values still fail."
                ),
                "reqlica.runtime.sdk.connection": (
                    "Read from the project's SQLite database with a context manager."
                ),
                "reqlica.runtime.sdk.transaction": (
                    "Write atomically with a context manager; commit on success, "
                    "roll back on error. Draft checks use disposable database copies."
                ),
            },
        }
