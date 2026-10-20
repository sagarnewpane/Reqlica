"""Offline regression coverage for prompt assembly and provider output contracts."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from reqlica.generation.clarification import review_requirements
from reqlica.generation.encoding import prompt_json
from reqlica.generation.errors import GenerationError
from reqlica.generation.json_output import parse_json
from reqlica.generation.prompts import (
    _check_prompt_size,
    build_messages,
    build_question_messages,
)
from reqlica.generation.schemas import (
    ClarificationRequest,
    GeneratedPlan,
    GenerationRequest,
    ProjectAnswer,
    ProjectQuestionRequest,
)
from reqlica.generation.service import _parse_proposal
from reqlica.projects.context import get_project_context
from reqlica.projects.store import create_project


class PromptContextTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "registry")})
        )
        self.project = create_project(self.root / "project", "Prompt contracts")
        self.request = GenerationRequest(requirements="Add GET /greeting")

    def test_review_limit_applies_to_review_not_discarded_generation_prompt(self):
        request = ClarificationRequest(requirements=self.request.requirements)
        with patch("reqlica.generation.clarification.provider.complete") as complete:
            complete.return_value = ('{"summary":"Ready","questions":[]}', None)
            review_requirements(self.project.id, request)
            review_messages = complete.call_args.args[0]
            limit = len(prompt_json(review_messages).encode("utf-8"))
            complete.reset_mock()
            with patch("reqlica.generation.prompts.MAX_PROMPT_BYTES", limit):
                result = review_requirements(self.project.id, request)
                self.assertEqual(result["status"], "ready")
                complete.assert_called_once()
                with self.assertRaises(GenerationError):
                    build_messages(self.project.id, self.request)
            complete.reset_mock()
            with patch("reqlica.generation.prompts.MAX_PROMPT_BYTES", limit - 1):
                with self.assertRaises(GenerationError):
                    review_requirements(self.project.id, request)
                complete.assert_not_called()

    def test_equivalent_context_maps_produce_identical_messages_without_data_loss(self):
        context = get_project_context(self.project.id, include_drafts=False)
        context["source"]["models/text.py"] = 'LABEL = "नमस्ते 🌍"\n'
        context["endpoints"] = [
            {"method": "GET", "path": "/items/special", "handler": "endpoints/items.py:special"},
            {"method": "GET", "path": "/items/{id}", "handler": "endpoints/items.py:by_id"},
        ]
        reordered = copy.deepcopy(context)
        reordered["source"] = dict(reversed(list(context["source"].items())))
        reordered["runtime_dependencies"] = dict(
            reversed(list(context["runtime_dependencies"].items()))
        )
        with patch("reqlica.generation.prompts.get_project_context", side_effect=[context, reordered]):
            _, first = build_messages(self.project.id, self.request)
            _, second = build_messages(self.project.id, self.request)
        self.assertEqual(first, second)
        payload = json.loads(first[1]["content"])
        self.assertEqual(payload["source"]["models/text.py"], context["source"]["models/text.py"])
        self.assertEqual(payload["endpoints"], context["endpoints"])
        self.assertIn("नमस्ते 🌍", first[1]["content"])

    def test_ai_context_skips_unused_schema_lookup_but_tool_context_keeps_it(self):
        with patch("reqlica.projects.context._api_summary", return_value={"status": "available"}) as summary:
            build_messages(self.project.id, self.request)
            build_question_messages(self.project.id, ProjectQuestionRequest(question="Explain /hello"))
            summary.assert_not_called()
            self.assertEqual(get_project_context(self.project.id)["api_schema"]["status"], "available")
            summary.assert_called_once()

    def test_size_boundary_counts_compact_utf8_without_silent_truncation(self):
        messages = [{"role": "user", "content": prompt_json({"requirements": "नमस्ते 🌍" * 100})}]
        original = copy.deepcopy(messages)
        size = len(prompt_json(messages).encode("utf-8"))
        self.assertLess(size, len(json.dumps(messages).encode("utf-8")))
        with patch("reqlica.generation.prompts.MAX_PROMPT_BYTES", size):
            _check_prompt_size(messages)
        with patch("reqlica.generation.prompts.MAX_PROMPT_BYTES", size - 1):
            with self.assertRaises(GenerationError) as failure:
                _check_prompt_size(messages)
            self.assertEqual(failure.exception.status_code, 413)
        self.assertEqual(messages, original)


class ResponseContractTests(unittest.TestCase):
    def test_provider_schemas_enforce_limits_before_oversized_output(self):
        cases = [
            (GeneratedPlan, {"summary": " ", "assumptions": [], "files": [], "endpoints": []}),
            (GeneratedPlan, {"summary": "Ready", "assumptions": ["x"] * 51, "files": [], "endpoints": []}),
            (GeneratedPlan, {"summary": "Ready", "assumptions": [], "files": [{"path": "endpoints/a.py", "content": ""}] * 21, "endpoints": []}),
            (GeneratedPlan, {"summary": "Ready", "assumptions": [], "files": [], "endpoints": [{"method": "GET", "path": "/", "handler": "endpoints/a.py:a"}] * 51}),
            (ProjectAnswer, {"answer": "x" * 32_769, "sources": [], "limitations": []}),
            (ProjectAnswer, {"answer": "Answer", "sources": ["sdk"] * 51, "limitations": []}),
            (ProjectAnswer, {"answer": "Answer", "sources": [], "limitations": ["x" * 4097]}),
        ]
        for model, value in cases:
            with self.subTest(model=model.__name__, fields=list(value)):
                validator = Draft202012Validator(model.model_json_schema())
                self.assertFalse(validator.is_valid(value))
                with self.assertRaises(ValidationError):
                    model.model_validate(value)

    def test_json_parsers_reject_nonfinite_numbers_even_in_nested_data(self):
        for parser in (parse_json, _parse_proposal):
            for constant in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(parser=parser.__name__, constant=constant):
                    with self.assertRaises(ValueError):
                        parser('{"values":[' + constant + ']}')
        # These character sequences remain legal inside source/string values.
        self.assertEqual(parse_json('{"source":"NaN"}'), {"source": "NaN"})


if __name__ == "__main__":
    unittest.main()
