"""Exercise the standalone dashboard's actual OpenAPI example helpers in Node."""

import shutil
import subprocess
import unittest
from pathlib import Path


class HTMLRequestExampleTests(unittest.TestCase):
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
