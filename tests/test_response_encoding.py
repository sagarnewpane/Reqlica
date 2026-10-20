import ast
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reqlica.runtime.sdk import APIJSONResponse
from reqlica.validation.source import name_issues


class Payload(BaseModel):
    created_at: datetime
    identifier: UUID
    amount: Decimal


class ResponseEncodingTests(unittest.TestCase):
    def test_http_nested_models_datetime_uuid_and_decimal(self):
        payload = Payload(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          identifier=UUID(int=1), amount=Decimal('12.50'))
        app = FastAPI()
        @app.get('/example')
        def example():
            response = APIJSONResponse({'items': [payload]}, status_code=201,
                                       headers={'X-Test': 'encoded'})
            response.set_cookie('example', 'value', httponly=True)
            return response
        with TestClient(app) as client:
            response = client.get('/example')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {'items': [payload.model_dump(mode='json')]})
        self.assertEqual(response.headers['x-test'], 'encoded')
        self.assertIn('HttpOnly', response.headers['set-cookie'])

    def test_unsupported_values_are_not_silently_stringified(self):
        with self.assertRaises((ValueError, TypeError)):
            APIJSONResponse({'unsupported': object()})

    def test_raw_model_dumps_are_caught_before_execution(self):
        for expression in ('model.model_dump()', '{"item": model.model_dump()}',
                           'model.model_dump_json()'):
            source = 'from fastapi.responses import JSONResponse as JR\ndef handler(model):\n return JR(content=' + expression + ')'
            issues = name_issues({'endpoints/example.py': ast.parse(source)})
            self.assertEqual(issues[0]['code'], 'unsafe_json_response')

    def test_valid_serialization_patterns_remain_allowed(self):
        for expression in ('model.model_dump(mode="json")',
                           'jsonable_encoder(model.model_dump())', '{"error": "bad request"}'):
            source = ('from starlette.responses import JSONResponse\n'
                      'from fastapi.encoders import jsonable_encoder\n'
                      'def handler(model):\n return JSONResponse(content=' + expression + ')')
            self.assertEqual(name_issues({'endpoints/example.py': ast.parse(source)}), [])
