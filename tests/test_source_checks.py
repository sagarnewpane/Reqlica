import ast
import unittest

from reqlica.validation.source import name_issues


class NameChecksTests(unittest.TestCase):
    def check(self, source):
        return name_issues({"endpoints/example.py": ast.parse(source)})

    def test_reports_missing_imports_annotations_and_unbound_locals(self):
        for source in (
            "def handler(): return JSONResponse({})",
            "def handler(value: MissingModel): return value",
            "value = 1\ndef handler():\n    print(value)\n    value = 2",
        ):
            with self.subTest(source=source):
                self.assertTrue(self.check(source))

    def test_understands_closures_forward_annotations_and_comprehensions(self):
        self.assertEqual(self.check('''from __future__ import annotations
def handler(value: Model):
    def inner(): return value
    return [inner() for item in range(2)]
class Model: pass
'''), [])

    def test_only_selected_files_are_checked(self):
        self.assertEqual(name_issues(
            {"models/legacy.py": ast.parse("value = missing")},
            paths={"endpoints/new.py"},
        ), [])

    def test_invalid_execution_context_is_rejected_without_import(self):
        with self.assertRaises(SyntaxError):
            self.check("return 1")
