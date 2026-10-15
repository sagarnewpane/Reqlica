"""Human verification references must resolve uniquely and remain project-scoped."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reqlica.projects import drafts
from reqlica.projects.references import (
    resolve_draft_reference,
    resolve_project_reference,
)
from reqlica.projects.store import create_project


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.enterContext(
            patch.dict(os.environ, {"REQLICA_HOME": str(self.root / "home")})
        )
        self.project = create_project(self.root / "todo", "Todo API")
        self.draft = drafts.create_draft(self.project.id)

    def test_names_and_prefixes_resolve_without_changing_ids(self):
        for reference in (self.project.id, self.project.id[:6], "Todo API", "todo api"):
            self.assertEqual(resolve_project_reference(reference), self.project.id)
        for reference in (self.draft["id"], self.draft["id"][:6]):
            self.assertEqual(
                resolve_draft_reference(self.project.id, reference), self.draft["id"]
            )

    def test_unknown_short_and_ambiguous_references_are_rejected(self):
        for reference in ("missing", "../escape", self.project.id[:3]):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                resolve_project_reference(reference)
        create_project(self.root / "duplicate", "Todo API")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            resolve_project_reference("Todo API")
        with (
            patch(
                "reqlica.projects.references.list_project_ids",
                return_value=["abcdef" + "a" * 26, "abcdef" + "b" * 26],
            ),
            self.assertRaisesRegex(ValueError, "Ambiguous"),
        ):
            resolve_project_reference("abcdef")

    def test_draft_prefixes_are_scoped_and_ambiguity_never_selects_a_draft(self):
        other = create_project(self.root / "other")
        with self.assertRaises(FileNotFoundError):
            resolve_draft_reference(other.id, self.draft["id"][:8])
        for reference in ("..", "bad", "A" * 8):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                resolve_draft_reference(self.project.id, reference)
        state = self.project.directory / ".reqlica/drafts"
        for suffix in ("a" * 26, "b" * 26):
            (state / ("abcdef" + suffix + ".json")).write_text("{}")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            resolve_draft_reference(self.project.id, "abcdef")
