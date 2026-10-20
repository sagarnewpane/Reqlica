"""Checkpoint recovery, schema correction, limits and duplicate-save protection."""
import json
import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation import jobs
from reqlica.generation.errors import GenerationError
from reqlica.generation.schemas import FileContent, GenerationOutline
from reqlica.projects import drafts
from test_generation import GenerationFixture, proposal, HANDLER, TEST_SOURCE


def outline():
    plan = proposal()
    return {**plan, 'interfaces': 'greet(name: str = "reader") -> Greeting(message: str)',
            'files': [{'path': file['path'], 'instructions': 'Implement the greeting contract.'} for file in plan['files']]}


class GenerationJobTests(GenerationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.id = uuid4().hex
        jobs.create_job(self.project.id, self.id, self.request)

    def step(self, payload):
        self.complete.side_effect = None
        self.complete.return_value = (json.dumps(payload), {'total_tokens': 10})
        return jobs.step_job(self.project.id, self.id)

    def test_one_file_per_step_and_completed_job_is_idempotent(self):
        result = self.step(outline())
        self.assertEqual(result['total_files'], 2)
        self.assertEqual(self.complete.call_args.kwargs['response_model'], GenerationOutline)
        self.assertEqual(drafts.list_drafts(self.project.id)['drafts'], [])
        result = self.step({'content': HANDLER})
        self.assertEqual(result['completed_files'], ['endpoints/greetings.py'])
        self.assertEqual(self.complete.call_args.kwargs['response_model'], FileContent)
        result = self.step({'content': TEST_SOURCE})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['total_tokens'], 30)
        self.assertEqual(result['result']['status'], 'draft_created')
        self.assertEqual(jobs.step_job(self.project.id, self.id)['draft_id'], result['draft_id'])
        self.assertEqual(self.complete.call_count, 3)
        self.assertEqual(len(drafts.list_drafts(self.project.id)['drafts']), 1)

    def test_schema_error_has_precise_feedback_and_recovers_next_step(self):
        result = self.step([])
        self.assertEqual(result['status'], 'retrying')
        issue = result['error']['validation_errors'][0]
        self.assertEqual(issue['path'], '<root>')
        self.assertEqual(issue['reason'], 'expected a JSON object')
        result = self.step(outline())
        correction = json.loads(self.complete.call_args.args[0][-1]['content'])
        self.assertEqual(correction['failed_response'], '[]')
        self.assertEqual(correction['errors']['code'], 'response_schema_mismatch')
        self.assertEqual(result['status'], 'ready')

    def test_openrouter_text_model_is_allowed_and_still_validates_output(self):
        request = self.request.model_copy(update={
            'provider': 'compatible', 'model': 'test/free-model',
            'base_url': 'https://openrouter.ai/api/v1',
        })
        self.id = uuid4().hex
        with patch('reqlica.generation.output_format.openrouter_model', return_value={'supported_parameters': []}):
            created = jobs.create_job(self.project.id, self.id, request)
        self.assertEqual(created['output_mode'], 'text')
        result = self.step([])
        self.assertEqual(result['status'], 'retrying')
        self.assertEqual(result['error']['code'], 'response_schema_mismatch')
        result = self.step(outline())
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(drafts.list_drafts(self.project.id)['drafts'], [])

    def test_failed_file_retains_completed_file_across_reload(self):
        self.step(outline())
        self.step({'content': HANDLER})
        self.complete.side_effect = GenerationError('Provider unavailable', 503)
        result = jobs.step_job(self.project.id, self.id)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['completed_files'], ['endpoints/greetings.py'])
        self.assertTrue(jobs.inspect_job(self.project.id, self.id)['can_resume'])
        result = self.step({'content': TEST_SOURCE})
        self.assertEqual(result['status'], 'completed')
        sent = json.loads(self.complete.call_args.args[0][-1]['content'])
        self.assertEqual(sent['target'], 'tests/test_greetings.py')
        self.assertEqual(sent['completed_files']['endpoints/greetings.py'], HANDLER)

    def test_bounded_retry_budget_survives_reloading_job(self):
        for _ in range(3):
            self.step(None)
        result = jobs.step_job(self.project.id, self.id)
        self.assertEqual(result['status'], 'exhausted')
        self.assertTrue(result['can_resume'])
        self.assertEqual(self.complete.call_count, 3)
        self.assertEqual(drafts.list_drafts(self.project.id)['drafts'], [])
        jobs.resume_job(self.project.id, self.id)
        jobs.resume_job(self.project.id, self.id)  # No repeated grant while ready.
        self.assertEqual(jobs._read(jobs._path(self.project.id, self.id))['limits']['plan'], 6)
        self.assertEqual(self.step(outline())['status'], 'ready')

    def test_stale_context_stops_before_provider_call(self):
        with patch('reqlica.generation.jobs._fingerprint', return_value='changed'):
            result = jobs.step_job(self.project.id, self.id)
        self.assertEqual(result['status'], 'stale')
        self.complete.assert_not_called()

    def test_plan_rejects_unsafe_paths_before_generating_source(self):
        value = outline()
        value['files'][0]['path'] = '../../escape.py'
        result = self.step(value)
        self.assertEqual(result['status'], 'retrying')
        self.assertEqual(result['completed_files'], [])
        self.assertIsNone(jobs._read(jobs._path(self.project.id, self.id))['outline'])

    def test_plan_clarification_creates_no_draft(self):
        result = self.step({**outline(), 'files': [], 'endpoints': []})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['status'], 'needs_information')
        self.assertEqual(drafts.list_drafts(self.project.id)['drafts'], [])

    def test_invalid_file_response_recovers_without_repeating_plan(self):
        self.step(outline())
        result = self.step({'content': None})
        self.assertEqual(result['status'], 'retrying')
        self.assertEqual(result['error']['validation_errors'][0]['path'], 'content')
        result = self.step({'content': HANDLER})
        self.assertEqual(result['stage'], 'tests/test_greetings.py')
        self.assertEqual(self.complete.call_count, 3)

    def test_interrupted_save_without_known_draft_id_never_retries_creation(self):
        path = jobs._path(self.project.id, self.id)
        job = jobs._read(path)
        job['status'] = 'saving'
        jobs._save(path, job)
        result = jobs.step_job(self.project.id, self.id)
        self.assertEqual(result['status'], 'save_failed')
        self.complete.assert_not_called()

    def test_changed_response_contract_invalidates_checkpoint(self):
        with patch('reqlica.generation.jobs._contract', return_value='changed'):
            result = jobs.step_job(self.project.id, self.id)
        self.assertEqual(result['status'], 'stale')
        self.complete.assert_not_called()

    def test_create_idempotent_and_public_data_omits_private_source(self):
        first = jobs.inspect_job(self.project.id, self.id)
        second = jobs.create_job(self.project.id, self.id, self.request)
        self.assertEqual(first, second)
        self.assertNotIn('request', first)
        self.assertNotIn('failed_response', first)
        with self.assertRaises(ValueError):
            jobs.create_job(self.project.id, self.id, self.request.model_copy(update={'requirements': 'other'}))

    def test_simultaneous_step_cannot_make_another_provider_call(self):
        with jobs._lease(jobs._path(self.project.id, self.id)):
            with self.assertRaises(GenerationError) as caught:
                jobs.step_job(self.project.id, self.id)
        self.assertEqual(caught.exception.status_code, 409)
        self.complete.assert_not_called()

    def test_save_failure_preserves_draft_id_without_creating_duplicate(self):
        self.step(outline())
        self.step({'content': HANDLER})
        with patch('reqlica.generation.service.drafts.write_draft_file', side_effect=OSError('disk failure')):
            result = self.step({'content': TEST_SOURCE})
        self.assertEqual(result['status'], 'save_failed')
        self.assertIsNotNone(result['draft_id'])
        result = jobs.step_job(self.project.id, self.id)
        self.assertEqual(result['status'], 'save_failed')
        self.assertEqual(len(drafts.list_drafts(self.project.id)['drafts']), 1)
        self.assertEqual(self.complete.call_count, 3)

    def test_integration_failure_repairs_assembly_without_regenerating_individual_files(self):
        self.step(outline())
        self.step({'content': HANDLER.replace('def greet(', 'def different(')})
        result = self.step({'content': TEST_SOURCE})
        self.assertEqual(result['stage'], 'assembly')
        self.assertEqual(result['status'], 'retrying')
        self.assertEqual(len(result['completed_files']), 2)
        result = self.step(proposal())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.complete.call_count, 4)

    def test_http_create_inspect_and_step(self):
        with TestClient(create_app()) as client:
            path = f'/api/projects/{self.project.id}/generation-jobs/{self.id}'
            response = client.put(path, json=self.request.model_dump())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.complete.return_value = (json.dumps(outline()), None)
            response = client.post(path + '/step')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['total_files'], 2)
            self.assertEqual(client.get(path).json()['id'], self.id)
            self.assertEqual(len(client.get(path.rsplit('/', 1)[0]).json()['jobs']), 1)
