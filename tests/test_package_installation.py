"""UI package reviews pin versions and cannot silently change the shared runtime."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from reqlica.console import packages
from reqlica.console.app import create_app
from reqlica.projects import drafts
from reqlica.projects.store import create_project
from reqlica.projects.versions import atomic_write_json, code_directory
from reqlica.runtime import dependencies, package_state

HASH = 'a' * 64


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, REQLICA_HOME=str(self.root / 'home')))
        self.project = create_project(self.root / 'api')
        self.other = create_project(self.root / 'other')
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.state_dir = self.runtime / '.reqlica-packages'
        self.enterContext(patch.object(packages, 'environment_directory', return_value=self.state_dir))
        self.enterContext(patch.object(package_state, 'environment_directory', return_value=self.state_dir))
        self.installed = {'core': '1.0'}
        self.enterContext(patch.object(packages, 'inventory', side_effect=lambda: dict(self.installed)))
        self.real_status = packages.installer_status
        self.enterContext(patch.object(packages, 'installer_status', return_value={'available':True, 'reason':None}))
        self.enterContext(patch.object(packages, '_launch', side_effect=lambda function, job, fd: function(job, fd)))
        self.commands = []
        self.real_command = packages._command
        self.command_patch = self.enterContext(patch.object(packages, '_command', side_effect=self.command))
        self.client = self.enterContext(TestClient(create_app(), client=('127.0.0.1', 50000)))
        self.url = f'/api/projects/{self.project.id}/packages'
        self.headers = {'X-Reqlica-Package-Action':'reviewed-ui'}

    def command(self, args, directory, timeout, fd):
        self.commands.append(args)
        os.fstat(fd)
        if args[:2] == ['pip', 'compile']:
            (directory / 'requirements.txt').write_text(f'demo-library==1.2.3 \\\n    --hash=sha256:{HASH}\ncore==1.0 --hash=sha256:{HASH}\n')
        else:
            self.assertRaises(ValueError, package_state.dependency_revision)
            self.installed['demo-library'] = '1.2.3'
        return 'test operation output'

    def preview(self):
        body = {'package':'demo-library', 'expected_revision':packages.fingerprint()}
        result = self.client.post(self.url + '/preview', json=body, headers=self.headers)
        self.assertEqual(result.status_code, 202, result.text)
        job = self.client.get(self.url + '/jobs/' + result.json()['id'])
        self.assertEqual(job.status_code, 200, job.text)
        self.assertEqual(job.json()['status'], 'review', job.text)
        return job.json()

    def install(self, job, **changes):
        return self.client.post(self.url + '/jobs/' + job['id'] + '/install',
            json={'expected_plan_digest':job['plan_digest'], 'confirm':True, **changes}, headers=self.headers)

    def test_info_scan_does_not_execute_source_and_suggests_only_known_distributions(self):
        marker = self.root / 'executed'
        (code_directory(self.project) / 'endpoints/hello.py').write_text(
            f'from pathlib import Path\nPath({str(marker)!r}).touch()\nimport argon2\nimport unknown_import_7392\ndef hello(): return {{}}\n')
        original = dependencies._available
        with patch.object(dependencies, '_available', side_effect=lambda name: False if name in {'argon2','unknown_import_7392'} else original(name)):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(marker.exists())
        issues = {i['module']:i for i in response.json()['scan']['issues']}
        self.assertEqual(issues['argon2']['suggested_package'], 'argon2-cffi')
        self.assertIsNone(issues['unknown_import_7392']['suggested_package'])

    def test_distribution_and_version_inputs_reject_commands_urls_extras_and_ranges(self):
        for name in ['-r', 'https://example.test/pkg.whl', './local', 'pkg;touch owned', 'pkg\nother', 'pkg[extra]', 'pkg==1']:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                packages.PackagePreview(package=name, expected_revision='a'*64)
        for version in ['>=1.2', '1 --index-url evil', '../path', '1;touch']:
            with self.subTest(version=version), self.assertRaises(ValidationError):
                packages.PackagePreview(package='okay', version=version, expected_revision='a'*64)
        self.assertEqual(self.commands, [])

    def test_preview_resolves_without_installing_and_pins_hashes_and_current_versions(self):
        job = self.preview()
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.commands[0][:2], ['pip', 'compile'])
        self.assertIn('--only-binary', self.commands[0])
        self.assertEqual(self.commands[0][self.commands[0].index('--only-binary')+1], ':all:')
        self.assertIn('--generate-hashes', self.commands[0])
        self.assertEqual((self.state_dir/job['id']/'constraints.txt').read_text(), 'core==1.0\n')
        self.assertEqual(job['packages'][0], {'name':'demo-library','version':'1.2.3','action':'install'})
        self.assertNotIn('installed_before', job)
        self.assertNotIn('demo-library', self.installed)

    def test_install_requires_separate_confirmation_and_never_replays(self):
        job = self.preview()
        self.assertEqual(self.install(job, confirm=False).status_code, 409)
        self.assertEqual(self.install(job, expected_plan_digest='0'*64).status_code, 409)
        self.assertEqual(len(self.commands), 1)
        result = self.install(job)
        self.assertEqual(result.status_code, 202, result.text)
        finished = self.client.get(self.url+'/jobs/'+job['id']).json()
        self.assertEqual(finished['status'], 'succeeded', finished)
        self.assertIn('--require-hashes', self.commands[1])
        self.assertIn('--no-deps', self.commands[1])
        self.assertEqual(package_state.read_state()['approved']['demo-library'], '1.2.3')
        self.assertNotEqual(package_state.dependency_revision(), package_state.EMPTY_REVISION)
        self.assertEqual(self.install(job).status_code, 409)
        self.assertEqual(len(self.commands), 2)

    def test_installed_changes_expired_previews_and_modified_lock_are_rejected(self):
        job = self.preview()
        self.installed['outside-change'] = '2'
        self.assertEqual(self.install(job).status_code, 409)
        del self.installed['outside-change']
        path = self.state_dir/job['id']/'requirements.txt'
        original = path.read_bytes()
        path.write_bytes(original+b'\nevil @ https://evil.test/a.whl\n')
        self.assertEqual(self.install(job).status_code, 409)
        path.write_bytes(original)
        record = packages._read(self.project.id, job['id'])
        record['prepared_at'] = 0
        packages._write(record)
        self.assertEqual(self.install(job).status_code, 409)
        self.assertEqual(len(self.commands), 1)

    def test_a_second_package_operation_cannot_enter_the_shared_environment(self):
        fd = packages._lock()
        try:
            response = self.client.post(self.url+'/preview', json={'package':'demo-library','expected_revision':packages.fingerprint()}, headers=self.headers)
            self.assertEqual(response.status_code, 409, response.text)
        finally:
            packages._unlock(fd)
        self.assertEqual(self.commands, [])

    def test_jobs_are_scoped_to_project_and_resume_from_saved_state(self):
        job = self.preview()
        self.assertEqual(self.client.get(f'/api/projects/{self.other.id}/packages/jobs/'+job['id']).status_code, 404)
        self.assertEqual(self.client.get(self.url+'/jobs/not-a-job').status_code, 404)
        info = self.client.get(self.url).json()
        self.assertEqual(info['jobs'][0]['id'], job['id'])
        self.assertEqual(info['jobs'][0]['status'], 'review')
        self.assertEqual(len(self.commands), 1)

    def test_interrupted_install_clears_in_progress_marker_and_invalidates_checks(self):
        job = self.preview()
        record = packages._read(self.project.id, job['id'])
        record.update(status='installing')
        packages._write(record)
        atomic_write_json(self.state_dir/'state.json', {'revision':'a'*32,'approved':{},'installing':job['id']})
        response = self.client.get(self.url+'/jobs/'+job['id'])
        self.assertEqual(response.json()['status'], 'interrupted', response.text)
        self.assertNotEqual(package_state.dependency_revision(), 'a'*32)
        self.assertFalse(package_state.read_state().get('installing'))
        self.assertEqual(self.install(job).status_code, 409)

    def test_success_and_partial_failure_invalidate_all_earlier_draft_checks(self):
        for fail in [False, True]:
            with self.subTest(fail=fail):
                self.installed.pop('demo-library', None)
                draft = drafts.create_draft(self.project.id)
                project = self.project
                state = drafts._state(project, draft['id'])
                revision = package_state.dependency_revision()
                state.update(validation={'revision':draft['revision'],'status':'passed','dependency_revision':revision},
                    test={'revision':draft['revision'],'status':'passed','dependency_revision':revision,'tests_run':1,'tests_passed':1},
                    approval={'revision':draft['revision']})
                drafts._save_state(project, draft['id'], state)
                job = self.preview()
                if fail:
                    def broken(args, directory, timeout, fd):
                        self.commands.append(args)
                        raise ValueError('Network interrupted')
                    self.command_patch.side_effect = broken
                result = self.install(job)
                self.assertEqual(result.status_code, 202, result.text)
                state = drafts.inspect_draft(self.project.id, draft['id'])
                self.assertIsNone(state['validation'])
                self.assertIsNone(state['test'])
                self.assertIsNone(state['approval'])
                self.assertFalse(package_state.read_state().get('installing'))
                self.assertEqual(self.client.get(self.url+'/jobs/'+job['id']).json()['status'], 'failed' if fail else 'succeeded')
                self.command_patch.side_effect = self.command

    def test_source_references_unhashed_plans_and_replacements_never_pass(self):
        for content in ['pkg @ https://example.test/pkg.whl', 'pkg==1.0', '--index-url https://evil.test', f'core==2.0 --hash=sha256:{HASH}', f'pkg==1.0 --hash=sha256:{HASH}\npkg==1.0 --hash=sha256:{HASH}']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                packages._parse_lock(content.encode(), {'core':'1.0'})

    def test_loopback_and_private_ui_header_are_required_for_writes(self):
        body = {'package':'demo-library','expected_revision':packages.fingerprint()}
        self.assertEqual(self.client.post(self.url+'/preview', json=body).status_code, 403)
        self.assertEqual(self.client.post(self.url+'/preview', json=body, headers={**self.headers,'Origin':'https://evil.test'}).status_code, 403)
        self.assertEqual(self.client.post(self.url+'/preview', json=body, headers={**self.headers,'Sec-Fetch-Site':'cross-site'}).status_code, 403)
        with TestClient(create_app(), client=('203.0.113.1', 9999)) as remote:
            self.assertEqual(remote.get(self.url).status_code, 403)
        self.assertEqual(self.commands, [])

    def test_ui_approved_distributions_are_available_to_ai_only_at_approved_versions(self):
        packages._root()
        atomic_write_json(self.state_dir/'state.json', {'revision':'b'*32,'approved':{'demo-library':'1.2.3'}})
        with patch.object(dependencies, '_declared_modules', return_value=()), \
             patch.object(dependencies.metadata, 'packages_distributions', return_value={'demolib':['Demo_Library']}), \
             patch.object(dependencies.metadata, 'version', return_value='1.2.3'), \
             patch.object(dependencies, '_available', return_value=True):
            self.assertEqual(dependencies.runtime_dependency_policy()['available']['demolib'], 'Demo_Library')
        with patch.object(dependencies, '_declared_modules', return_value=()), \
             patch.object(dependencies.metadata, 'packages_distributions', return_value={'demolib':['Demo_Library']}), \
             patch.object(dependencies.metadata, 'version', return_value='9.0'), \
             patch.object(dependencies, '_available', return_value=True):
            self.assertNotIn('demolib', dependencies.runtime_dependency_policy()['available'])

    def test_checks_cannot_start_during_installation_or_store_results_across_package_changes(self):
        from reqlica.validation import checks
        draft = drafts.create_draft(self.project.id)
        packages._root()
        atomic_write_json(self.state_dir/'state.json', {'revision':'a'*32,'approved':{},'installing':'job'})
        with self.assertRaisesRegex(ValueError, 'installation is in progress'):
            drafts.inspect_draft(self.project.id, draft['id'])
        atomic_write_json(self.state_dir/'state.json', {'revision':'b'*32,'approved':{}})
        def result(_job, _workspace):
            atomic_write_json(self.state_dir/'state.json', {'revision':'c'*32,'approved':{}})
            return {'status':'passed','output':'test result','tests_run':1,'tests_passed':1}
        with patch.object(checks, '_execute', side_effect=result), self.assertRaisesRegex(ValueError, 'Runtime packages changed during the check'):
            checks.test_draft(self.project.id, draft['id'], draft['revision'])
        self.assertIsNone(drafts.inspect_draft(self.project.id, draft['id'])['test'])

    def test_installer_process_drops_secret_environment_and_has_a_real_timeout(self):
        import sys
        directory = self.root/'command'
        directory.mkdir()
        executable = self.root/'fake-uv'
        executable.write_text(f'#!{sys.executable}\nimport os,json\nprint(json.dumps(dict(os.environ)))\n')
        executable.chmod(0o700)
        fd = packages._lock()
        try:
            with patch.object(packages.shutil, 'which', return_value=str(executable)), \
                 patch.dict(os.environ, {'JWT_SECRET':'never-forward-this','UV_EXTRA_INDEX_URL':'https://secret.invalid','PYTHONPATH':'/untrusted'}):
                output = self.real_command(['pip','install'], directory, 3, fd)
            environment = json.loads(output)
            self.assertNotIn('JWT_SECRET', environment)
            self.assertNotIn('UV_EXTRA_INDEX_URL', environment)
            self.assertNotIn('PYTHONPATH', environment)
            executable.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(10)\n')
            with patch.object(packages.shutil, 'which', return_value=str(executable)), self.assertRaisesRegex(ValueError, 'timed out'):
                self.real_command(['pip','install'], directory, 0.05, fd)
        finally:
            packages._unlock(fd)

    def test_gateway_environment_must_match_the_console_and_be_local(self):
        import sys
        from types import SimpleNamespace
        from reqlica.runtime.app import app as gateway
        response = TestClient(gateway).get('/api/runtime-environment')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['prefix'], str(Path(sys.prefix).resolve()))
        valid = {'prefix':str(Path(sys.prefix).resolve()),'python_version':list(sys.version_info[:2])}
        def reply(body):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
        with patch('reqlica.cli._mock_base_url', return_value='http://127.0.0.1:4000'), \
             patch.object(packages.httpx, 'get', return_value=reply(valid)):
            self.assertTrue(packages.gateway_runtime_status()['matches'])
        with patch('reqlica.cli._mock_base_url', return_value='http://127.0.0.1:4000'), \
             patch.object(packages.httpx, 'get', return_value=reply({**valid,'prefix':'/wrong/runtime'})):
            self.assertFalse(packages.gateway_runtime_status()['matches'])
        with patch('reqlica.cli._mock_base_url', return_value='http://203.0.113.1:4000'), \
             patch.object(packages.httpx, 'get') as request:
            self.assertFalse(packages.gateway_runtime_status()['matches'])
            request.assert_not_called()
        with patch.object(packages, 'gateway_runtime_status', return_value={'matches':False,'reason':'Wrong environment'}):
            self.assertEqual(self.real_status(), {'available':False,'reason':'Wrong environment'})


class RealPackageInstallTests(unittest.TestCase):
    def test_offline_mcp_installs_reviewed_wheels(self):
        self.run_offline_probe(mcp=True)

    def test_offline_wheels_install_into_disposable_runtime_with_transitive_dependency(self):
        self.run_offline_probe()

    def run_offline_probe(self, mcp=False):
        import shutil
        import subprocess
        from package_install_fixture import create_runtime
        if not shutil.which('uv'):
            self.skipTest('uv is required for the real offline installer test')
        with tempfile.TemporaryDirectory(prefix='reqlica-package-integration-') as directory:
            root = Path(directory).resolve()
            python, tools = create_runtime(root)
            result = subprocess.run([str(python), str(Path(__file__).with_name('package_install_fixture.py')), str(root), *(['mcp'] if mcp else [])],
                env={**os.environ, 'PATH':str(tools)+os.pathsep+os.environ['PATH'], 'REQLICA_HOME':str(root/'home')},
                capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'succeeded')
