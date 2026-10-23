"""User-reviewed, version-preserving wheel installs into the running virtualenv.

No shell, build scripts, URLs, custom indexes, upgrade/removal operations, or unattended
installation. A persisted resolution must be reviewed before a separate POST
can install its hash-pinned wheels. Closing the browser never replays a write.
"""

import ast
import fcntl
import hashlib
import importlib
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from importlib import metadata
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import httpx

from pydantic import BaseModel, ConfigDict, Field
from reqlica.projects.store import get_project
from reqlica.projects.versions import atomic_write_json, code_directory, project_lock, _read_code_file
from reqlica.runtime import dependencies
from reqlica.runtime.package_state import environment_directory, read_state, normalize

NAME = r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?'
VERSION = r'[A-Za-z0-9][A-Za-z0-9.!+_-]{0,79}'
ACTIVE = {'resolving', 'installing'}
LIMIT = 1024 * 1024


class PackagePreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    package: str = Field(pattern=rf'^{NAME}$')
    version: str | None = Field(default=None, pattern=rf'^{VERSION}$')
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')


class PackageInstall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_plan_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool = Field(strict=True)


def inventory():
    packages = {}
    for dist in metadata.distributions():
        name = dist.metadata.get('Name')
        if not name or not re.fullmatch(NAME, name) or not re.fullmatch(VERSION, dist.version):
            raise ValueError('Runtime has invalid distribution metadata; repair its virtual environment first')
        name = normalize(name)
        if name in packages and packages[name] != dist.version:
            raise ValueError('Runtime contains conflicting installed versions; repair its virtual environment first')
        packages[name] = dist.version
    return dict(sorted(packages.items()))


def fingerprint(packages=None):
    return hashlib.sha256(json.dumps({'prefix': str(Path(sys.prefix).resolve()),
        'python': sys.executable, 'packages': inventory() if packages is None else packages,
        'state': read_state()}, sort_keys=True).encode()).hexdigest()


def installer_status():
    if sys.prefix == sys.base_prefix:
        return {'available': False, 'reason': 'Start Reqlica from a virtual environment; system Python installs are disabled.'}
    if not shutil.which('uv'):
        return {'available': False, 'reason': 'uv is not available to the console process. Install uv and restart Reqlica.'}
    if not os.access(sys.prefix, os.W_OK):
        return {'available': False, 'reason': 'The running virtual environment is not writable.'}
    gateway = gateway_runtime_status()
    if not gateway['matches']:
        return {'available': False, 'reason': gateway['reason']}
    return {'available': True, 'reason': None}


def gateway_runtime_status():
    from reqlica.cli import _mock_base_url
    try:
        base = _mock_base_url()
        parsed = urlsplit(base)
        if parsed.scheme != 'http' or not (parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback):
            raise ValueError('Package installs require a loopback gateway')
        response = httpx.get(base + '/api/runtime-environment', timeout=3, trust_env=False)
        response.raise_for_status()
        data = response.json()
        if data.get('prefix') != str(Path(sys.prefix).resolve()) or data.get('python_version') != list(sys.version_info[:2]):
            return {'matches': False, 'reason': 'Console and gateway use different Python environments. Start both from the same virtual environment before installing.'}
        return {'matches': True, 'reason': None}
    except (httpx.HTTPError, OSError, ValueError, AttributeError, TypeError):
        return {'matches': False, 'reason': 'Cannot verify the gateway Python environment. Start/restart the updated gateway from the same virtual environment as the console, then reload packages.'}


def _root():
    root = environment_directory()
    if root.is_symlink():
        raise ValueError('Runtime package state must not be a symlink')
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def _lock():
    path = _root() / 'install.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ValueError('Another package operation is running in this environment; wait and reload') from None
    return fd


def _unlock(fd):
    os.close(fd)


def _directory(identity):
    if not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise FileNotFoundError('Unknown package job')
    path = environment_directory() / identity
    if path.is_symlink():
        raise ValueError('Package job must not be a symlink')
    return path


def _read(project_id, identity):
    get_project(project_id)
    path = _directory(identity) / 'job.json'
    if path.is_symlink() or path.stat().st_size > LIMIT:
        raise ValueError('Invalid package job')
    job = json.loads(path.read_bytes())
    if job.get('id') != identity or job.get('project_id') != project_id:
        raise FileNotFoundError('Unknown package job for this project')
    return job


def _write(job):
    atomic_write_json(_directory(job['id']) / 'job.json', job)


def _public(job):
    return {key: value for key, value in job.items() if key not in {'installed_before', 'requirements_digest'}}


def inspect_job(project_id, identity):
    job = _read(project_id, identity)
    if job['status'] in ACTIVE:
        try:
            fd = _lock()
        except ValueError:
            pass
        else:
            try:
                # A dead console cannot leave the UI polling forever. A previous
                # install may be partial, so a fresh resolution is mandatory.
                job = _read(project_id, identity)
                if job['status'] in ACTIVE:
                    state = read_state()
                    if state.get('installing') == identity:
                        atomic_write_json(environment_directory() / 'state.json',
                            {**state, 'revision': uuid4().hex, 'installing': None})
                    job.update(status='interrupted', message='Console stopped during this operation. Reload packages and resolve again; installation may be partial.')
                    _write(job)
            finally:
                _unlock(fd)
    return _public(job)


def scan_imports(project_id, draft_id=None):
    from reqlica.projects.drafts import draft_directory, _state
    project = get_project(project_id)
    with project_lock(project.directory):
        directory = code_directory(project)
        revision = None
        if draft_id:
            revision = _state(project, draft_id)['revision']
            directory = draft_directory(project, draft_id)
        trees = {}
        for path in directory.rglob('*.py'):
            if '__pycache__' in path.parts:
                continue
            if len(trees) >= 256:
                raise ValueError('Import scan exceeds 256 Python files')
            relative = path.relative_to(directory)
            raw = _read_code_file(directory, relative)
            if len(raw) > LIMIT:
                raise ValueError('Python source exceeds the import-scan limit')
            trees[relative.as_posix()] = ast.parse(raw, filename=str(relative))
        issues = dependencies.import_issues(trees)
    for issue in issues:
        root = issue['module'].split('.')[0]
        issue['suggested_package'] = (dependencies.KNOWN_PACKAGES.get(root)
            if issue['code'] == 'missing_dependency' and root not in sys.stdlib_module_names else None)
    return {'draft_id': draft_id, 'revision': revision, 'issues': issues}


def package_info(project_id, draft_id=None):
    get_project(project_id)
    packages = inventory()
    jobs = []
    root = environment_directory()
    for path in sorted(root.glob('*/job.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        try:
            job = _read(project_id, path.parent.name)
            jobs.append(inspect_job(project_id, job['id']))
        except (OSError, ValueError, KeyError):
            continue
        if len(jobs) >= 10:
            break
    try:
        scan = scan_imports(project_id, draft_id)
    except (ValueError, SyntaxError) as error:
        scan = {'draft_id': draft_id, 'issues': [], 'error': str(error)[:1024]}
    return {'project_id': project_id, 'prefix': str(Path(sys.prefix).resolve()), 'python': sys.executable,
        'shared': True, 'revision': fingerprint(packages), 'installer': installer_status(),
        'installed': packages, 'approved': read_state()['approved'], 'scan': scan, 'jobs': jobs}


def _command(args, directory, timeout, lock_fd):
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('uv is no longer available; restart Reqlica after installing uv')
    # Do not inherit project secrets, pip/uv source overrides, Python injection,
    # proxy credentials, or ambient installer settings into package processes.
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'SYSTEMROOT') if key in os.environ}
    command = [uv, '--no-config', '--no-python-downloads', '--no-progress', '--cache-dir', str(_root() / 'cache'), '--color', 'never', *args]
    with (directory / 'output.log').open('wb') as output:
        process = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                                   pass_fds=(lock_fd,))
        deadline = time.monotonic() + timeout
        reason = None
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or output.tell() > LIMIT:
                    reason = 'Package operation timed out' if time.monotonic() >= deadline else 'Package output exceeded 1 MiB'
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.1)
            process.wait()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    log_path = directory / 'output.log'
    if log_path.stat().st_size > LIMIT:
        reason = reason or 'Package output exceeded 1 MiB'
    with log_path.open('rb') as output:
        output.seek(max(0, log_path.stat().st_size - 16384))
        log = output.read(16384).decode('utf-8', errors='replace')
    if reason:
        raise ValueError(reason + '. Reload environment state before retrying.\n' + log)
    if process.returncode:
        raise ValueError('uv could not complete the package operation.\n' + log)
    return log


def _parse_lock(content, before):
    if len(content) > 2 * LIMIT:
        raise ValueError('Resolved package plan exceeds 2 MiB')
    entries = []
    for line in content.decode('utf-8').replace('\\\n', ' ').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(rf'({NAME})==({VERSION})((?:\s+--hash=sha256:[0-9a-f]{{64}})+)', line)
        if not match:
            raise ValueError('Resolver returned an unsupported requirement; only hash-pinned registry wheels are accepted')
        name, version = normalize(match[1]), match[2]
        if name in before and before[name] != version:
            raise ValueError('Installation would replace an existing package; shared runtime upgrades are disabled')
        if any(item['name'] == name for item in entries):
            raise ValueError('Duplicate distribution in resolved package plan')
        entries.append({'name': name, 'version': version, 'action': 'keep' if name in before else 'install'})
    if not entries or len(entries) > 128:
        raise ValueError('Resolve between 1 and 128 distributions')
    return entries


def _prepare(job, fd):
    try:
        directory = _directory(job['id'])
        (directory / 'package.in').write_text(job['requirement'] + '\n')
        (directory / 'constraints.txt').write_text(''.join(f'{name}=={version}\n' for name, version in job['installed_before'].items()))
        log = _command(['pip', 'compile', 'package.in', '--constraints', 'constraints.txt', '--python', sys.executable,
            '--only-binary', ':all:', '--no-sources', '--default-index', 'https://pypi.org/simple',
            '--generate-hashes', '--no-annotate', '--no-header', '--output-file', 'requirements.txt'], directory, 120, fd)
        lock_path = directory / 'requirements.txt'
        if lock_path.is_symlink() or lock_path.stat().st_size > 2 * LIMIT:
            raise ValueError('Invalid or oversized resolved package plan')
        content = lock_path.read_bytes()
        packages = _parse_lock(content, job['installed_before'])
        if not any(item['name'] == normalize(job['package']) for item in packages):
            raise ValueError('Resolved plan does not contain the requested distribution')
        if fingerprint() != job['environment_revision']:
            raise ValueError('Runtime packages changed during resolution; reload and resolve again')
        digest = hashlib.sha256(content).hexdigest()
        job.update(status='review', packages=packages, requirements_digest=digest, log=log,
            plan_digest=hashlib.sha256((digest + job['environment_revision'] + job['id']).encode()).hexdigest(),
            prepared_at=time.time(), message='Review the exact versions, then confirm installation. Nothing has been installed.')
    except Exception as error:
        job.update(status='failed', message=str(error)[:16384])
    finally:
        try:
            _write(job)
        finally:
            _unlock(fd)


def _launch(target, job, fd):
    try:
        threading.Thread(target=target, args=(job, fd), daemon=True).start()
    except Exception:
        _unlock(fd)
        raise


def preview_package(project_id, body):
    get_project(project_id)
    status = installer_status()
    if not status['available']:
        raise ValueError(status['reason'])
    fd = _lock()
    try:
        packages = inventory()
        if fingerprint(packages) != body.expected_revision:
            raise ValueError('Runtime packages changed; reload before resolving')
        identity = uuid4().hex
        _directory(identity).mkdir(mode=0o700)
        job = {'id': identity, 'project_id': project_id, 'package': body.package, 'version': body.version,
            'requirement': body.package + ('==' + body.version if body.version else ''), 'status': 'resolving',
            'created_at': time.time(), 'environment_revision': body.expected_revision,
            'installed_before': packages, 'message': 'Resolving compatible wheels from PyPI. No installation yet.'}
        _write(job)
    except Exception:
        _unlock(fd)
        raise
    result = _public(job)
    _launch(_prepare, job, fd)
    return result


def _install(job, fd):
    try:
        directory = _directory(job['id'])
        path = directory / 'requirements.txt'
        if path.is_symlink() or path.stat().st_size > 2 * LIMIT or hashlib.sha256(path.read_bytes()).hexdigest() != job['requirements_digest']:
            raise ValueError('Reviewed package plan changed; resolve again')
        before = read_state()
        # Invalidate old checks even if installation is interrupted or partial.
        atomic_write_json(environment_directory() / 'state.json', {**before, 'revision': uuid4().hex, 'installing': job['id']})
        log = _command(['pip', 'install', '--python', sys.executable, '--no-deps', '--require-hashes',
            '--only-binary', ':all:', '--no-sources', '--default-index', 'https://pypi.org/simple',
            '--requirements', 'requirements.txt'], directory, 300, fd)
        importlib.invalidate_caches()
        after = inventory()
        for item in job['packages']:
            if after.get(item['name']) != item['version']:
                raise ValueError('Installed versions do not match the reviewed plan; reload packages and resolve again')
        if any(after.get(name) != version for name, version in job['installed_before'].items()):
            raise ValueError('An existing package changed unexpectedly; inspect the shared runtime before continuing')
        state = read_state()
        approved = {**state['approved'], **{item['name']: item['version'] for item in job['packages']}}
        atomic_write_json(environment_directory() / 'state.json', {**state, 'approved': approved})
        dependencies._declared_modules.cache_clear()
        job.update(status='succeeded', log=log, message='Reviewed packages installed and approved. Restart affected APIs, then rerun validation and tests. Existing checks and approval were invalidated.')
    except Exception as error:
        job.update(status='failed', message=str(error)[:16384] + '\nInstallation may be partial. Reload packages before resolving again.')
    finally:
        try:
            state = read_state()
            if state.get('installing') == job['id']:
                atomic_write_json(environment_directory() / 'state.json',
                    {**state, 'revision': uuid4().hex, 'installing': None})
            _write(job)
        finally:
            _unlock(fd)


def install_package(project_id, identity, body):
    if not body.confirm:
        raise ValueError('Explicit installation confirmation is required')
    status = installer_status()
    if not status['available']:
        raise ValueError(status['reason'])
    fd = _lock()
    try:
        job = _read(project_id, identity)
        if job['status'] != 'review':
            raise ValueError('This job cannot be installed again; reload its saved result')
        if time.time() - job['prepared_at'] > 1800 or job['plan_digest'] != body.expected_plan_digest:
            raise ValueError('Package preview expired or changed; resolve again')
        if fingerprint() != job['environment_revision']:
            raise ValueError('Runtime packages changed since preview; reload and resolve again')
        path = _directory(identity) / 'requirements.txt'
        if path.is_symlink() or path.stat().st_size > 2 * LIMIT or hashlib.sha256(path.read_bytes()).hexdigest() != job['requirements_digest']:
            raise ValueError('Reviewed package plan changed; resolve again')
        _parse_lock(path.read_bytes(), job['installed_before'])
        job.update(status='installing', message='Installing the reviewed hash-pinned wheels. Keep using the UI or reload to check the saved result.')
        _write(job)
    except Exception:
        _unlock(fd)
        raise
    result = _public(job)
    _launch(_install, job, fd)
    return result
