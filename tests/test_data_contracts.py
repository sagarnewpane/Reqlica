"""Mock records must match simple app contracts; complex records are refused."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from reqlica.console.app import create_app
from reqlica.generation.data_schemas import (
    DataImportRequest, DataValidationRequest, MockDataRequest,
    RelatedDataImportRequest, RelatedDataValidationRequest, RelatedMockDataRequest,
)
from reqlica.generation.errors import GenerationError
from reqlica.generation.mock_data import generate_mock_data, generate_related_mock_data
from reqlica.projects.data_contracts import application_data_contract, require_mockable
from reqlica.projects.mock_data import database_schema, insert_data, validate_data
from reqlica.projects.related_data import insert_related_data, validate_related_data
from reqlica.projects.store import create_project
from reqlica.projects.versions import code_directory

USER_ID = '6d78b014-caf3-45b3-9f25-77e0105e2bbc'
POST_ID = '32bb4aee-a6e0-4258-a09b-cd5bc8b6c39c'
MODELS = '''from uuid import UUID
from pydantic import BaseModel, Field
class UserOut(BaseModel):
    id: UUID
    username: str = Field(min_length=3, max_length=30)
class PostOut(BaseModel):
    id: UUID
    author_id: UUID
    title: str = Field(min_length=1)
'''


class DataContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {'REQLICA_HOME': str(self.root / 'home')}))
        self.project = create_project(self.root / 'app', 'UUID app')
        self.path = self.project.directory / 'data.sqlite'
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE users(id TEXT PRIMARY KEY NOT NULL, username TEXT NOT NULL UNIQUE);
                CREATE TABLE posts(id TEXT PRIMARY KEY NOT NULL, author_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL);''')
        self.code = code_directory(self.project)
        (self.code / 'models').mkdir(exist_ok=True)
        self.model_path = self.code / 'models' / 'records.py'
        self.model_path.write_text(MODELS)
        self.complete = self.enterContext(patch('reqlica.generation.mock_data.provider.complete'))

    def schema(self):
        return database_schema(self.project.id)['fingerprint']

    def records(self):
        return {'users': [{'id': USER_ID, 'username': 'ava'}], 'posts': [{'id': POST_ID, 'author_id': {'$ref': 'users.0.id'}, 'title': 'Hello'}]}

    def request(self, records=None):
        return RelatedDataValidationRequest(expected_schema=self.schema(), records_by_table=self.records() if records is None else records)

    def output(self, records=None, refusal=None):
        return (json.dumps({'summary': 'Related UUID rows', 'assumptions': [], 'records_by_table_json': json.dumps(self.records() if records is None else records), 'refusal_reason': refusal}), {})

    def test_valid_uuid_rows_can_be_read_as_the_application_expects(self):
        request = self.request()
        preview = validate_related_data(self.project.id, request)
        self.assertEqual(preview['validation']['status'], 'passed', preview)
        insert_related_data(self.project.id, RelatedDataImportRequest(**request.model_dump(), expected_preview_digest=preview['preview_digest'], confirm=True))
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT p.id,p.author_id,u.id FROM posts p JOIN users u ON p.author_id=u.id').fetchone()
        self.assertEqual([UUID(value) for value in row], [UUID(POST_ID), UUID(USER_ID), UUID(USER_ID)])

    def test_sqlite_text_acceptance_does_not_allow_a_bad_uuid(self):
        data = self.records()
        data['users'][0]['id'] = 'user_1'
        preview = validate_related_data(self.project.id, self.request(data))
        self.assertEqual(preview['validation']['status'], 'failed')
        self.assertEqual(preview['validation']['issues'][0]['expected'], 'uuid')
        with self.assertRaises(GenerationError):
            insert_related_data(self.project.id, RelatedDataImportRequest(**self.request(data).model_dump(), expected_preview_digest=preview['preview_digest'], confirm=True))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 0)

    def test_invalid_generated_uuid_is_refused_once_not_repaired_or_saved(self):
        data = self.records()
        data['users'][0]['id'] = 'not-a-uuid'
        self.complete.return_value = self.output(data)
        request = RelatedMockDataRequest(requirements='Create a user and a post', expected_schema=self.schema())
        with self.assertRaises(GenerationError) as caught:
            generate_related_mock_data(self.project.id, request)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail['code'], 'mock_data_unsupported')
        self.assertIn('users[0].id', str(caught.exception))
        self.complete.assert_called_once()

    def test_single_table_generation_and_import_check_uuid_and_model_lengths(self):
        request = DataValidationRequest(target='sqlite', table='users', expected_schema=self.schema(), records=[{'id': USER_ID, 'username': 'a'}])
        preview = validate_data(self.project.id, request)
        self.assertEqual(preview['validation']['status'], 'failed')
        self.assertEqual(preview['validation']['issues'][0]['field'], 'username')
        request = request.model_copy(update={'records': [{'id': 'bad', 'username': 'ava'}]})
        self.complete.return_value = (json.dumps({'summary': 'One user', 'assumptions': [], 'records_json': json.dumps(request.records)}), {})
        with self.assertRaisesRegex(GenerationError, "can't be mocked"):
            generate_mock_data(self.project.id, MockDataRequest(target='sqlite', table='users', expected_schema=self.schema(), count=1, requirements='One user'))
        self.complete.assert_called_once()
        preview = validate_data(self.project.id, request)
        with self.assertRaises(GenerationError):
            insert_data(self.project.id, DataImportRequest(**request.model_dump(), expected_preview_digest=preview['preview_digest'], confirm=True))

    def test_password_hash_users_are_refused_before_provider_call_or_preview(self):
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE users ADD COLUMN password_hash TEXT')
        request = RelatedMockDataRequest(requirements='create 2 users', expected_schema=self.schema())
        with self.assertRaisesRegex(GenerationError, 'password_hash'):
            generate_related_mock_data(self.project.id, request)
        self.complete.assert_not_called()
        data = self.records()
        data['users'][0]['password_hash'] = 'scrypt$16384$8$1$BBBB$BBBB'
        with self.assertRaisesRegex(GenerationError, 'application-specific hashing'):
            validate_related_data(self.project.id, self.request(data))

    def test_custom_validators_and_nested_storage_are_refused(self):
        for field in ('username: str\n    @field_validator("username")\n    @classmethod\n    def custom(cls, value):\n        return value', 'username: list[str]'):
            self.model_path.write_text('from uuid import UUID\nfrom pydantic import BaseModel, field_validator\nclass UserOut(BaseModel):\n    id: UUID\n    ' + field + '\n')
            with self.subTest(field=field), self.assertRaisesRegex(GenerationError, "can't be mocked"):
                validate_related_data(self.project.id, self.request())
        self.complete.assert_not_called()

    def test_inherited_custom_validation_cannot_bypass_refusal(self):
        self.model_path.write_text("from pydantic import BaseModel, field_validator\nclass Common(BaseModel):\n    username: str\n    @field_validator('username')\n    @classmethod\n    def check(cls, value):\n        return value\nclass UserOut(Common):\n    id: str\n")
        with self.assertRaisesRegex(GenerationError, 'custom Python validation'):
            validate_related_data(self.project.id, self.request())

    def test_source_revision_change_invalidates_previously_reviewed_import(self):
        request = self.request()
        preview = validate_related_data(self.project.id, request)
        self.model_path.write_text(MODELS.replace('max_length=30', 'max_length=10'))
        with self.assertRaisesRegex(ValueError, 'application changed'):
            insert_related_data(self.project.id, RelatedDataImportRequest(**request.model_dump(), expected_preview_digest=preview['preview_digest'], confirm=True))

    def test_generic_model_uuid_cast_is_mapped_through_sql_without_running_source(self):
        marker = self.root / 'executed'
        self.model_path.write_text('from pathlib import Path\nPath('+repr(str(marker))+').touch()\nfrom uuid import UUID\nfrom pydantic import BaseModel\nclass RecordOut(BaseModel):\n    id: UUID\ndef read_rows(db):\n    rows=db.execute("SELECT id FROM users")\n    return [RecordOut(id=UUID(row["id"])) for row in rows]\n')
        contract = application_data_contract(self.project.id, database_schema(self.project.id))
        self.assertFalse(marker.exists())
        self.assertEqual(contract['fields']['users']['id'][0]['schema']['format'], 'uuid')
        request = DataValidationRequest(target='sqlite', table='users', expected_schema=self.schema(), records=[{'id':'bad','username':'ava'}])
        self.assertEqual(validate_data(self.project.id, request)['validation']['status'], 'failed')

    def test_provider_can_explicitly_refuse_and_api_reports_clear_message(self):
        self.complete.return_value = self.output(refusal='This requires a custom storage encoding.')
        with TestClient(create_app()) as client:
            response = client.post(f'/api/projects/{self.project.id}/data/related/generate', json={'requirements':'Create a user', 'expected_schema':self.schema()})
        self.assertEqual(response.status_code, 422)
        self.assertIn("This data can't be mocked reliably", response.json()['detail']['message'])
        self.assertEqual(response.json()['detail']['code'], 'mock_data_unsupported')
        self.complete.assert_called_once()

    def test_external_or_recursive_schemas_are_refused_without_resolving_them(self):
        from reqlica.projects.data_contracts import _schema_problem
        self.assertIsNotNone(_schema_problem({'$ref':'https://example.com/schema.json'}, {}))
        self.assertIsNotNone(_schema_problem({'$ref':'#/components/schemas/Loop'}, {'Loop':{'$ref':'#/components/schemas/Loop'}}))

    def test_foreign_key_propagates_known_uuid_format_to_parent_id(self):
        self.model_path.write_text('from uuid import UUID\nfrom pydantic import BaseModel\nclass PostOut(BaseModel):\n    id: UUID\n    author_id: UUID\n')
        contract = application_data_contract(self.project.id, database_schema(self.project.id))
        self.assertEqual(contract['fields']['users']['id'][0]['schema']['format'], 'uuid')
        require_mockable(contract, ['users','posts'])
