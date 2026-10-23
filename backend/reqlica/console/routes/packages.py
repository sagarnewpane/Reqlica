"""Browser-controlled package review/install endpoints; never exposed as MCP tools."""

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from reqlica.console import packages
from reqlica.console.dependencies import draft_operation


def local_package_request(request: Request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
        origin = request.headers.get('origin')
        if origin:
            host = urlsplit(origin).hostname
            local = local and (host == 'localhost' or ipaddress.ip_address(host).is_loopback)
    except (ValueError, AttributeError):
        local = False
    if not local or request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Package management requires a loopback dashboard connection')
    if request.method == 'POST' and request.headers.get('x-reqlica-package-action') != 'reviewed-ui':
        raise HTTPException(403, 'Use the dashboard package review and confirmation controls')


router = APIRouter(prefix='/api/projects/{project_id}/packages', tags=['Runtime packages'],
                   dependencies=[Depends(local_package_request)])


@router.get('')
def listing(project_id: str, response: Response, draft_id: str | None = None):
    response.headers['Cache-Control'] = 'no-store'
    return draft_operation(packages.package_info, project_id, draft_id)


@router.post('/preview', status_code=202)
def preview(project_id: str, body: packages.PackagePreview):
    return draft_operation(packages.preview_package, project_id, body)


@router.get('/jobs/{job_id}')
def job(project_id: str, job_id: str, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return draft_operation(packages.inspect_job, project_id, job_id)


@router.post('/jobs/{job_id}/install', status_code=202)
def install(project_id: str, job_id: str, body: packages.PackageInstall):
    return draft_operation(packages.install_package, project_id, job_id, body)
