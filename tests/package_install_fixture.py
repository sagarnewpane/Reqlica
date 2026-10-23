"""Author tiny wheels locally and install only into disposable QA virtualenvs."""

import base64
import hashlib
import shutil
import sys
import venv
import zipfile
from pathlib import Path


def wheel(directory, distribution, module, source, requires=None):
    normalized = distribution.replace('-', '_')
    info = normalized + '-1.0.dist-info'
    files = {
        module + '.py': source.encode(),
        info + '/METADATA': (f'Metadata-Version: 2.1\nName: {distribution}\nVersion: 1.0\n' + (f'Requires-Dist: {requires}\n' if requires else '') + '\n').encode(),
        info + '/WHEEL': b'Wheel-Version: 1.0\nGenerator: Reqlica QA\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    records = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
        records.append(f'{name},sha256={digest},{len(content)}\n')
    files[info + '/RECORD'] = (''.join(records) + info + '/RECORD,,\n').encode()
    path = directory / (normalized + '-1.0-py3-none-any.whl')
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def create_runtime(root):
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is required for offline installer QA')
    root = Path(root).resolve()
    directory = root / 'runtime'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(directory)
    site = directory / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    sources = [str(Path(__file__).parents[1] / 'backend')]
    sources.extend(entry for entry in sys.path if entry.endswith('site-packages'))
    (site / 'reqlica-qa.pth').write_text('\n'.join(dict.fromkeys(sources)) + '\n')
    wheels = root / 'wheels'
    wheels.mkdir()
    wheel(wheels, 'Reqlica-UI-Helper', 'reqlica_ui_helper', 'VALUE = "installed"\n')
    wheel(wheels, 'Reqlica-UI-Probe', 'reqlica_ui_probe', 'from reqlica_ui_helper import VALUE\ndef probe(): return VALUE\n', 'Reqlica-UI-Helper==1.0')
    tools = root / 'qa-tools'
    tools.mkdir()
    # This fixture-only wrapper redirects uv to our own two local wheels. The
    # production resolver still uses PyPI and never accepts a caller-supplied index.
    wrapper = tools / 'uv'
    wrapper.write_text(f'#!{sys.executable}\nimport os, sys\nos.execv({uv!r}, [{uv!r}, *sys.argv[1:], "--no-index", "--find-links", {str(wheels)!r}])\n')
    wrapper.chmod(0o700)
    return directory / 'bin/python', tools


def probe_install(root):
    import time
    from reqlica.console import packages
    from reqlica.projects.store import create_project
    from reqlica.runtime.dependencies import runtime_dependency_policy
    from unittest.mock import patch
    # The browser smoke test verifies the real gateway match. This subprocess
    # fixture exercises installer mechanics without binding network sockets.
    gateway_check = patch.object(packages, 'gateway_runtime_status', return_value={'matches':True, 'reason':None})
    gateway_check.start()
    project = create_project(root / 'project')
    job = packages.preview_package(project.id, packages.PackagePreview(
        package='Reqlica-UI-Probe', expected_revision=packages.fingerprint()))
    def wait(current):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = packages.inspect_job(project.id, current['id'])
            if current['status'] not in packages.ACTIVE:
                return current
            time.sleep(0.05)
        raise AssertionError(current)
    job = wait(job)
    assert job['status'] == 'review', job
    assert {p['name'] for p in job['packages']} == {'reqlica-ui-probe', 'reqlica-ui-helper'}, job
    job = wait(packages.install_package(project.id, job['id'], packages.PackageInstall(
        expected_plan_digest=job['plan_digest'], confirm=True)))
    assert job['status'] == 'succeeded', job
    import reqlica_ui_probe
    assert reqlica_ui_probe.probe() == 'installed'
    assert 'reqlica_ui_probe' in runtime_dependency_policy()['available']
    return job


def probe_mcp_install(root):
    import json
    import time
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from reqlica.console import packages
    from reqlica.console.app import create_app
    from reqlica.projects.store import create_project
    project = create_project(root / 'project')
    with patch.object(packages, 'gateway_runtime_status', return_value={'matches':True,'reason':None}), TestClient(create_app(), base_url='http://127.0.0.1:3000') as client:
        counter = 0
        def rpc(method, params):
            nonlocal counter
            counter += 1
            response = client.post('/mcp', headers={'Accept':'application/json, text/event-stream'}, json={'jsonrpc':'2.0','id':counter,'method':method,'params':params})
            assert response.status_code == 200, response.text
            result = response.json()
            assert 'error' not in result, result
            return result['result']
        rpc('initialize', {'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'offline-package-test','version':'1'}})
        def tool(name, **arguments):
            result = rpc('tools/call', {'name':name,'arguments':{'project_id':project.id, **arguments}})
            assert not result.get('isError'), result
            return json.loads(result['content'][0]['text'])
        info = tool('inspect_runtime_packages')
        assert 'installed' not in info, info
        job = tool('preview_package_install', package='Reqlica-UI-Probe', expected_environment_revision=info['revision'])
        def wait(job):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                job = tool('get_package_install_job', job_id=job['id'])
                if job['status'] not in packages.ACTIVE:
                    return job
                time.sleep(.05)
            raise AssertionError(job)
        job = wait(job)
        assert job['status'] == 'review', job
        assert {p['name'] for p in job['packages']} == {'reqlica-ui-probe','reqlica-ui-helper'}, job
        job = wait(tool('install_package', job_id=job['id'], expected_plan_digest=job['plan_digest'], confirm=True))
        assert job['status'] == 'succeeded', job
        import reqlica_ui_probe
        assert reqlica_ui_probe.probe() == 'installed'
        return job


if __name__ == '__main__':
    import json
    probe = probe_mcp_install if len(sys.argv) > 2 and sys.argv[2] == 'mcp' else probe_install
    print(json.dumps(probe(Path(sys.argv[1]))))
