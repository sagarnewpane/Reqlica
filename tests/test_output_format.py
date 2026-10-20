"""Select advertised formats, require routing support, never guess duplicate values."""

import json
import unittest
from unittest.mock import patch

import httpx
from reqlica.generation import catalog
from reqlica.generation.errors import GenerationError
from reqlica.generation.output_format import output_mode, format_parameters, format_instructions
from reqlica.generation.schemas import GeneratedPlan, GenerationRequest, ClarificationReview
from reqlica.generation.service import _parse_proposal


class OutputFormatTests(unittest.TestCase):
    def test_openrouter_formats_follow_advertised_support(self):
        request = GenerationRequest(requirements="x", provider="compatible", model="test", base_url=catalog.OPENROUTER_BASE)
        with patch("reqlica.generation.output_format.openrouter_model", return_value={"supported_parameters": ["structured_outputs"]}):
            self.assertEqual(output_mode(request), "json_schema")
        for parameters, expected in ((["response_format"], "json_object"), ([], "text")):
            with patch("reqlica.generation.output_format.openrouter_model", return_value={"supported_parameters": parameters}):
                self.assertEqual(output_mode(request), expected)
        self.assertEqual(format_parameters("text", "proposal", GeneratedPlan), {})
        wire = format_parameters("json_schema", "proposal", GeneratedPlan)["response_format"]
        self.assertTrue(wire["json_schema"]["strict"])
        self.assertEqual(wire["json_schema"]["schema"], GeneratedPlan.model_json_schema())

    def test_fallback_instructions_teach_separate_objects_and_escaped_source(self):
        instructions = format_instructions(GeneratedPlan)
        self.assertIn("EVERY nesting level", instructions)
        self.assertIn("separate array objects", instructions)
        self.assertIn('"path":"models/first.py"', instructions)
        self.assertIn('"path":"models/second.py"', instructions)
        self.assertIn("escaped newlines", instructions)
        self.assertIn('"$defs"', instructions)
        self.assertNotIn('"$defs"', format_instructions(GeneratedPlan, include_schema=False))
        self.assertNotIn("Serialization example", format_instructions(ClarificationReview))

    def test_duplicate_diagnostics_identify_structure_without_echoing_untrusted_keys(self):
        with self.assertRaisesRegex(ValueError, r"proposal.files\[0\].*content"):
            _parse_proposal('{"files":[{"path":"endpoints/a.py","content":"first","content":"second"}]}')
        with self.assertRaises(ValueError) as error:
            _parse_proposal('{"files":[{"private-secret-key":"first","private-secret-key":"second"}]}')
        self.assertNotIn("private-secret-key", str(error.exception))
        self.assertNotIn("first", str(error.exception))

    def test_parser_does_not_extract_objects_from_prose_or_multiple_fences(self):
        for text in (
            'Here is the proposal: {"files":[]}',
            '```json\n{"files":[]}\n```\nTrailing commentary',
            '```json\n{"files":[]}\n```\n```json\n{"files":[]}\n```',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                _parse_proposal(text)

    def test_valid_complex_source_round_trips_without_normalization(self):
        source = 'TEXT = """say "hello"\\n"""\nREGEX = r"\\d+\\s"\nDATA = {"key": "value"}\n'
        payload = {"files": [{"path": "models/example.py", "content": source}]}
        parsed, warnings = _parse_proposal(json.dumps(payload))
        self.assertEqual(parsed, payload)
        self.assertEqual(warnings, [])

    def test_trailing_commas_are_removed_without_changing_source(self):
        source = 'VALUE = ",}"\nOTHER = ",]"\nQUOTED = "\\\""\n'
        text = '{"files":[{"path":"models/example.py","content":' + json.dumps(source) + ',},],}'
        parsed, warnings = _parse_proposal(text)
        self.assertEqual(parsed["files"][0]["content"], source)
        self.assertEqual(warnings, ["Removed trailing JSON commas outside strings."])

    def test_comma_recovery_does_not_fill_missing_values_or_hide_duplicates(self):
        for text in ('{"files":[,]}', '{"files":[1,,]}', '{"files":,}', '{"files":[],"files":null,}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                _parse_proposal(text)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _parse_proposal('{"files":[{"content":"first","content":"second",}],}')


class CatalogTests(unittest.TestCase):
    def tearDown(self):
        catalog._catalog = {}
        catalog._cached_at = 0

    def test_fixed_public_catalog_is_cached_and_carries_no_authorization(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"data": [{"id": "test", "reasoning": {"mandatory": False}, "supported_parameters": ["structured_outputs"]}]})

        original_client = httpx.Client
        with patch("reqlica.generation.catalog.httpx.Client", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs)):
            self.assertEqual(catalog.openrouter_model("test")["supported_parameters"], ["structured_outputs"])
            self.assertEqual(catalog.openrouter_model("test")["reasoning"], {"mandatory": False})
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), catalog.OPENROUTER_BASE + "/models")
        self.assertNotIn("authorization", requests[0].headers)

    def test_malformed_metadata_is_a_clear_error_without_silent_downgrade(self):
        original_client = httpx.Client
        with patch("reqlica.generation.catalog.httpx.Client", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": "bad"})), **kwargs)):
            with self.assertRaises(GenerationError) as error:
                catalog.openrouter_model("test")
        self.assertEqual(error.exception.status_code, 503)
