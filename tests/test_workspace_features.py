"""Workspace persistence and historical restoration never activate code."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.projects.store import create_project, get_project
from reqlica.projects import drafts

class WorkspaceFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, REQLICA_HOME=str(self.root / 'home')))
        self.project = create_project(self.root / 'api', 'Workspace')
        self.client = self.enterContext(TestClient(create_app()))
        self.url = f'/api/projects/{self.project.id}'

    def test_library_round_trip_conflict_and_soft_delete(self):
        identity = uuid4().hex
        path = self.url + '/library/' + identity
        data = {'kind':'dataset','name':'Events','data':{'mode':'json','source':'[{"id":1}]'}}
        response = self.client.put(path, json=data)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        listed = self.client.get(self.url + '/library').json()['items']
        self.assertEqual(listed[0]['data'], data['data'])
        self.assertEqual(self.client.put(path, json=data).status_code, 409)
        response = self.client.request('DELETE', path, json={'expected_revision':saved['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(self.url + '/library').json()['items'], [])
        self.assertTrue((self.project.directory/'.reqlica/library'/f'{identity}.json').exists())

    def test_version_restore_creates_unchecked_draft_and_preserves_active(self):
        history = self.client.get(self.url + '/versions').json()
        version = history['versions'][0]
        response = self.client.post(self.url + '/versions/restore', json={
            'version_id':version['id'],'expected_revision':history['active_revision'],
            'expected_version_revision':version['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        restored = response.json()
        self.assertEqual(restored['restored_from_version'], version['id'])
        self.assertIsNone(restored['validation'])
        self.assertIsNone(restored['test'])
        self.assertIsNone(restored['approval'])
        self.assertEqual(get_project(self.project.id).active_version, version['id'])
        self.assertEqual(restored['base_version'], version['id'])

    def test_stale_restore_and_unsafe_library_ids_are_rejected(self):
        version = self.client.get(self.url+'/versions').json()['versions'][0]
        response=self.client.post(self.url+'/versions/restore',json={
            'version_id':version['id'],'expected_revision':'0'*64,'expected_version_revision':version['revision']})
        self.assertEqual(response.status_code,409)
        self.assertEqual(drafts.list_drafts(self.project.id)['drafts'],[])
        response=self.client.put(self.url+'/library/invalid',json={'kind':'request','name':'Read','data':{}})
        self.assertEqual(response.status_code,409)
