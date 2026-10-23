"""Project operations through existing revision-checked domain and gateway APIs."""

import json
from typing import Annotated, Literal

import httpx
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from reqlica.cli import _mock_base_url
from reqlica.console.routes.maintenance import maintenance_state
from reqlica.mcp.parameters import ProjectID
from reqlica.projects import authentication, cors, reset, scenarios
from reqlica.projects.migrations import migration_history
from reqlica.runtime.operations import WorkerControl
from reqlica.runtime.relocation import Relocation

Confirm = Annotated[bool, Field(strict=True, description="True only after the human explicitly authorizes this exact operation and target; never infer from a request to diagnose or generate code.")]
ActiveRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="Exact active_revision from get_worker_status; inspect again on conflict.")]
BaselineRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", description="Exact revision from get_data_baseline; all zeros means no baseline yet.")]
ScenarioRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", description="Exact scenarios.revision from get_traffic_policies.")]
Schema = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="Exact current_schema_fingerprint from get_data_baseline.")]


def require_confirmation(confirm):
    if confirm is not True:
        raise ValueError("Explicit human confirmation is required")


def gateway_request(project_id, command, body=None, *, method="POST", binary=False):
    """Never retry a maintenance request after an uncertain acknowledgement."""
    try:
        reply = httpx.request(method, f"{_mock_base_url()}/api/projects/{project_id}/{command}",
                              json=body, timeout=120, trust_env=False)
    except httpx.HTTPError as error:
        raise ValueError("Gateway request failed; inspect current state before retrying: " + str(error)) from error
    if not reply.is_success:
        try:
            detail = reply.json().get("detail", "Operation failed")
        except (ValueError, AttributeError):
            detail = reply.text[:2048]
        raise ValueError(f"Gateway {reply.status_code}: {str(detail)[:2048]}")
    if binary:
        return reply.content
    try:
        result = reply.json()
        if not isinstance(result, dict):
            raise ValueError("Expected an object")
        return result
    except ValueError as error:
        raise ValueError("Malformed gateway reply; inspect state before retrying") from error


async def get_traffic_policies(project_id: ProjectID) -> dict:
    """Read scenarios/quotas, CORS and authentication metadata; never returns credentials.

    Each policy has its own revision. Scenarios affect published traffic only;
    remaining_failures is output-only. Authentication changes stay in the dashboard.
    """
    def read():
        return {"scenarios": scenarios.get_scenarios(project_id),
                "cors": cors.get_cors(project_id),
                "authentication": authentication.get_authentication(project_id)}
    return await run_in_threadpool(read)


async def set_traffic_policy(
    project_id: ProjectID,
    policy: Annotated[Literal["scenarios", "cors"], Field(description="Published gateway policy to replace; authentication secrets use the dashboard.")],
    expected_revision: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{32}|[0-9a-f]{64})$", description="Exact revision of the selected policy from get_traffic_policies.")],
    settings_json: Annotated[str, Field(max_length=65536, description='Replacement JSON object, without revision/project_id. scenarios: {"rules":[{"method":"GET","path":"/hello","delay_ms":100,"response":"failure","status_code":503,"failure_count":2,"rate_limit":{"requests":5,"window_seconds":60,"scope":"route"}}]}; response also passthrough/empty. cors: enabled,allow_origins,allow_methods,allow_headers,expose_headers,allow_credentials,max_age; omitted fields use defaults. Never copy remaining_failures.')],
    confirm: Confirm,
) -> dict:
    """Replace a reviewed live traffic policy with an exact revision and human approval.

    Scenarios replace ALL rules and reset quotas/failure counters; [] disables them.
    CORS requires explicit origins when credentials are enabled. No worker restart.
    """
    require_confirmation(confirm)
    settings = json.loads(settings_json)
    if not isinstance(settings, dict) or "expected_revision" in settings:
        raise ValueError("Use a settings object without expected_revision")
    model, write = (scenarios.ScenarioWrite, scenarios.write_scenarios) if policy == "scenarios" else (cors.CorsWrite, cors.write_cors)
    body = model.model_validate({**settings, "expected_revision": expected_revision})
    return await run_in_threadpool(write, project_id, body)


async def reset_scenario_counters(project_id: ProjectID, expected_revision: ScenarioRevision, confirm: Confirm) -> dict:
    """Reset published failure counts and rate-limit buckets, preserving rules.

    Require explicit human approval; returns a new scenario revision.
    """
    require_confirmation(confirm)
    return await run_in_threadpool(scenarios.reset_scenario_counters, project_id, expected_revision)


async def get_worker_status(project_id: ProjectID) -> dict:
    """Read gateway worker health plus current directory/active_revision; never starts it.

    Code revision is read separately from worker state; control rechecks it atomically.
    An unavailable gateway is an error, never inferred stopped status.
    """
    state = await run_in_threadpool(maintenance_state, project_id)
    worker = await run_in_threadpool(gateway_request, project_id, "endpoints", method="GET")
    return {**worker, **state}


async def control_project_worker(project_id: ProjectID,
    action: Annotated[Literal["start", "stop", "restart"], Field(description="Explicit lifecycle action for this published API only.")],
    expected_revision: ActiveRevision, confirm: Confirm) -> dict:
    """Control a published worker after human approval and a current code revision check.

    Start/restart executes trusted project Python with OS/network access. Stop may
    interrupt requests and prevents lazy restart. Restart does not publish drafts.
    On timeout/failure inspect get_worker_status before any retry.
    """
    body = WorkerControl(action=action, expected_revision=expected_revision, confirm=confirm)
    return await run_in_threadpool(gateway_request, project_id, "worker-control", body.model_dump())


async def get_data_baseline(project_id: ProjectID) -> dict:
    """Read baseline metadata and current SQLite schema fingerprint; no records."""
    return await run_in_threadpool(reset.baseline_info, project_id)


async def capture_data_baseline(project_id: ProjectID, expected_revision: BaselineRevision, confirm: Confirm) -> dict:
    """Snapshot current SQLite data as the reset baseline after human approval.

    Replaces the baseline pointer; retains older snapshots. Does not restore data
    or stop workers. The snapshot may contain sensitive application records.
    """
    body = reset.ConfirmedRevision(expected_revision=expected_revision, confirm=confirm)
    return await run_in_threadpool(reset.capture_baseline, project_id, body)


async def reset_project_data(project_id: ProjectID, expected_revision: BaselineRevision,
    expected_schema: Schema, confirm: Confirm) -> dict:
    """Restore live SQLite from the reviewed baseline; discards changes since capture.

    Requires human approval, matching baseline/schema. Gateway drains/stops the
    worker and keeps a pre-reset backup; worker stays stopped. Inspect baseline
    and worker status after an uncertain reply, never blindly repeat the reset.
    """
    body = reset.DataReset(expected_revision=expected_revision, expected_schema=expected_schema, confirm=confirm)
    return await run_in_threadpool(gateway_request, project_id, "data-reset", body.model_dump())


async def list_database_migrations(project_id: ProjectID,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum receipts per page.")] = 20,
    offset: Annotated[int, Field(ge=0, description="Zero-based page offset; use returned next_offset.")] = 0) -> dict:
    """Read saved migration receipts, phases, schema/draft pins and backup paths.

    No database records or live migration execution. Ordered by receipt filename,
    not execution time; pagination can shift during concurrent maintenance.
    """
    return await run_in_threadpool(migration_history, project_id, limit=limit, offset=offset)


async def relocate_project(project_id: ProjectID, expected_revision: ActiveRevision,
    expected_directory: Annotated[str, Field(description="Exact current directory from get_worker_status.")],
    directory: Annotated[str, Field(description="New absolute server-local directory with an existing parent, on the same filesystem.")],
    confirm: Confirm) -> dict:
    """Move the complete project, preserving identity, after human destination approval.

    Gateway drains/stops the worker; it stays stopped. Rejects overlap, replacement
    and cross-filesystem moves. Inspect registry/worker status after uncertain reply.
    """
    body = Relocation(expected_revision=expected_revision, expected_directory=expected_directory, directory=directory, confirm=confirm)
    return await run_in_threadpool(gateway_request, project_id, "relocate", body.model_dump())


def register_operation_tools(server):
    reads = (get_traffic_policies, get_worker_status, get_data_baseline, list_database_migrations)
    writes = (set_traffic_policy, reset_scenario_counters, control_project_worker,
              capture_data_baseline, reset_project_data, relocate_project)
    for operation in reads + writes:
        server.add_tool(operation, annotations=ToolAnnotations(
            readOnlyHint=operation in reads, destructiveHint=operation in writes,
            idempotentHint=operation in reads, openWorldHint=operation is control_project_worker),
            meta={"reqlica/category": "core"})
