"""Reviewed package operations for human-authorized MCP clients."""

from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from reqlica.console import packages
from reqlica.mcp.parameters import ProjectID, DraftID

JobID = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$', description='Saved package job ID returned by preview_package_install or inspect_runtime_packages.')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$', description='Exact plan_digest returned by get_package_install_job; never invent it.')]


async def inspect_runtime_packages(project_id: ProjectID,
    draft_id: Annotated[DraftID | None, Field(description="Optional unpublished draft ID from create_draft.id or list_drafts.drafts[].id; omit to scan published source.")] = None,
    include_installed: Annotated[bool, Field(description="Include the full installed distribution/version inventory; default omits it to save context.")] = False) -> dict:
    """Inspect missing imports, target Python environment and recent package jobs.

    No handler execution or installation. Installed inventory is opt-in to limit
    context. Known distribution suggestions are hints; verify unfamiliar names.
    Use revision for preview_package_install.expected_environment_revision.
    """
    result = await run_in_threadpool(packages.package_info, project_id, draft_id)
    result['installed_count'] = len(result['installed'])
    if not include_installed:
        result.pop('installed')
    result['jobs'] = [{key: job.get(key) for key in ('id', 'requirement', 'status', 'plan_digest')}
                      for job in result['jobs']]
    return result


async def preview_package_install(
    project_id: ProjectID,
    package: Annotated[str, Field(pattern=rf'^{packages.NAME}$', description='Verified PyPI distribution name, e.g. PyJWT for import jwt. No paths, URLs, extras or flags.')],
    expected_environment_revision: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$', description='revision from inspect_runtime_packages, not a draft hash.')],
    version: Annotated[str | None, Field(pattern=rf'^{packages.VERSION}$', description='Optional exact version; no ranges.')] = None,
) -> dict:
    """Resolve a user-requested package into a saved hash-pinned wheel plan.

    Downloads metadata; installs nothing. Poll get_package_install_job until
    review/failed. Existing versions cannot be replaced. Show the target environment
    and all install versions to the human before requesting install confirmation.
    """
    body = packages.PackagePreview(package=package, version=version,
                                   expected_revision=expected_environment_revision)
    return await run_in_threadpool(packages.preview_package, project_id, body)


async def get_package_install_job(project_id: ProjectID, job_id: JobID,
    include_output: Annotated[bool, Field(description="Include bounded installer output and the full failure message; default returns compact status.")] = False) -> dict:
    """Read a saved package resolution/install result without retrying writes.

    review includes exact packages/actions and plan_digest. resolving/installing
    are still running; succeeded/failed/interrupted are terminal. Output is opt-in.
    A timeout or lost acknowledgement requires reading this job, never blind replay.
    """
    result = await run_in_threadpool(packages.inspect_job, project_id, job_id)
    if not include_output:
        result.pop('log', None)
        result['message'] = result.get('message', '')[:1024]
    return result


async def install_package(
    project_id: ProjectID, job_id: JobID, expected_plan_digest: Digest,
    confirm: Annotated[bool, Field(strict=True, description='True only after the human explicitly authorizes this exact reviewed package plan and shared target environment.')],
) -> dict:
    """Install an exact reviewed package plan after explicit human authorization.

    Changes the shared virtualenv for ALL projects. Require human approval of the
    versions and target; do not infer it from a request to fix/generate an endpoint.
    Existing versions are kept; only hashed wheels. Checks/approval are invalidated.
    Returns an asynchronous job; poll its ID. Restart affected APIs and rerun checks.
    Never repeat after uncertain acknowledgement: inspect the saved job first.
    """
    body = packages.PackageInstall(expected_plan_digest=expected_plan_digest, confirm=confirm)
    return await run_in_threadpool(packages.install_package, project_id, job_id, body)


def register_package_tools(server):
    for operation in (inspect_runtime_packages, get_package_install_job):
        server.add_tool(operation, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
                        meta={'reqlica/category': 'core'})
    server.add_tool(preview_package_install,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
        meta={'reqlica/category': 'core'})
    server.add_tool(install_package,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
        meta={'reqlica/category': 'core'})
