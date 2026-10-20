"""Exercise the standalone dashboard's actual OpenAPI example helpers in Node."""

import shutil
import subprocess
import unittest
from pathlib import Path


class HTMLRequestExampleTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed for HTML helper tests")
    def test_reasoning_controls(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("html_reasoning.cjs"))],
            check=False, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for HTML helper tests")
    def test_ai_activity(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("html_activity.cjs"))],
            check=False, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for HTML helper tests")
    def test_feature_ui(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("html_feature_ui.cjs"))],
            check=False, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for HTML helper tests")
    def test_bounded_draft_repair(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("html_repair.cjs"))],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for HTML helper tests")
    def test_generation_clarifications(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("html_generation.cjs"))],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for HTML helper tests")
    def test_chat_history(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("html_chat.cjs"))],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for HTML helper tests")
    def test_request_examples(self):
        script = Path(__file__).with_name("html_request_examples.cjs")
        result = subprocess.run(
            ["node", "--test", str(script)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
