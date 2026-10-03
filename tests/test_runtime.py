"""Check the mock server's demo endpoint and startup configuration."""

import os
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from reqlica.runtime.app import app, main


class MockServerTests(unittest.TestCase):
    def test_hello(self):
        with TestClient(app) as client:
            response = client.get("/mock/demo/hello")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"message": "Hello from Reqlica", "project_id": "demo"},
        )

    def test_startup_defaults(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("reqlica.runtime.app.load_dotenv") as load_env,
            patch("reqlica.runtime.app.uvicorn.run") as run_server,
        ):
            main()

        load_env.assert_called_once_with(
            Path(__file__).resolve().parents[1] / ".env"
        )
        run_server.assert_called_once_with(app, host="127.0.0.1", port=4000)

    def test_startup_uses_loaded_settings(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "reqlica.runtime.app.load_dotenv",
                side_effect=lambda _: load_dotenv(
                    stream=StringIO("MOCK_HOST=127.0.0.2\nMOCK_PORT=4100\n")
                ),
            ),
            patch("reqlica.runtime.app.uvicorn.run") as run_server,
        ):
            main()

        run_server.assert_called_once_with(app, host="127.0.0.2", port=4100)
