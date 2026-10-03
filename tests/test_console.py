"""Check the console's health endpoint and startup configuration."""

import os
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from dotenv import load_dotenv

from reqlica.console.app import app, main


class ConsoleTests(unittest.TestCase):
    def test_health(self):
        with TestClient(app) as client:
            response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"status": "ok", "service": "reqlica-console"}
        )

    def test_startup_defaults(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("reqlica.console.app.load_dotenv") as load_env,
            patch("reqlica.console.app.uvicorn.run") as run_server,
        ):
            main()

        load_env.assert_called_once_with(
            Path(__file__).resolve().parents[1] / ".env"
        )
        run_server.assert_called_once_with(app, host="127.0.0.1", port=3000)

    def test_startup_uses_loaded_settings(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "reqlica.console.app.load_dotenv",
                side_effect=lambda _: load_dotenv(
                    stream=StringIO("CONSOLE_HOST=127.0.0.2\nCONSOLE_PORT=3100\n")
                ),
            ),
            patch("reqlica.console.app.uvicorn.run") as run_server,
        ):
            main()

        run_server.assert_called_once_with(app, host="127.0.0.2", port=3100)
