"""Exercise the published CLI workflow over real console and gateway sockets."""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

import httpx

WORKSPACE = Path(__file__).resolve().parents[1]
LOG_LIMIT = 8192

RECORD_SOURCE = '''"""Stateful fixture with an explicitly pre-created schema."""

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from reqlica.runtime.sdk import connection, transaction


class RecordInput(BaseModel):
    value: str = Field(min_length=1)


def list_records() -> list[dict]:
    with connection() as database:
        return [dict(row) for row in database.execute(
            "SELECT id, value FROM records ORDER BY id"
        )]


def create_record(request: Request, body: RecordInput) -> dict:
    with transaction() as database:
        cursor = database.execute(
            "INSERT INTO records (value) VALUES (?)", (body.value,)
        )
        return {"id": cursor.lastrowid, "value": body.value, "method": request.method}


def fail_record(body: RecordInput) -> dict:
    with transaction() as database:
        database.execute("INSERT INTO records (value) VALUES (?)", (body.value,))
        raise HTTPException(status_code=500, detail="fixture rollback")
'''

RECORD_TEST = '''"""Test normal handler imports, input validation, and atomic writes."""

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from endpoints.records import create_record, fail_record, list_records


class RecordTests(unittest.TestCase):
    def test_records(self):
        app = FastAPI()
        app.add_api_route("/records", list_records, methods=["GET"])
        app.add_api_route("/records", create_record, methods=["POST"])
        app.add_api_route("/records/fail", fail_record, methods=["POST"])
        with TestClient(app, raise_server_exceptions=False) as client:
            baseline = client.get("/records").json()
            self.assertEqual(baseline, [{"id": 1, "value": "baseline"}])
            self.assertEqual(client.post("/records", json={"value": ""}).status_code, 422)
            created = client.post("/records", json={"value": "test-only"})
            self.assertEqual(created.status_code, 200)
            self.assertEqual(created.json(), {"id": 2, "value": "test-only", "method": "POST"})
            saved = [*baseline, {"id": 2, "value": "test-only"}]
            self.assertEqual(client.get("/records").json(), saved)
            failed = client.post("/records/fail", json={"value": "discarded"})
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(failed.json(), {"detail": "fixture rollback"})
            self.assertEqual(client.get("/records").json(), saved)
'''

RECORD_ROUTES = [
    {
        "method": "GET",
        "path": "/records",
        "handler": "endpoints/records.py:list_records",
    },
    {
        "method": "POST",
        "path": "/records",
        "handler": "endpoints/records.py:create_record",
    },
    {
        "method": "POST",
        "path": "/records/fail",
        "handler": "endpoints/records.py:fail_record",
    },
]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="reqlica-workflow-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.environment = {
            **os.environ,
            "REQLICA_HOME": str(self.root / "home"),
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    [
                        str(WORKSPACE / "backend"),
                        os.environ.get("PYTHONPATH", ""),
                    ],
                )
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CONSOLE_HOST": "127.0.0.1",
            "MOCK_HOST": "127.0.0.1",
        }
        self.logs = []
        self.console_socket = self.reserve_port()
        self.gateway_socket = self.reserve_port()
        self.console_port = self.console_socket.getsockname()[1]
        self.gateway_port = self.gateway_socket.getsockname()[1]
        self.environment.update(
            CONSOLE_PORT=str(self.console_port), MOCK_PORT=str(self.gateway_port)
        )
        self.console_url = f"http://127.0.0.1:{self.console_port}"
        self.gateway_url = f"http://127.0.0.1:{self.gateway_port}"
        self.client = self.enterContext(httpx.Client(trust_env=False, timeout=20))

    def reserve_port(self, port=0):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        return listener

    def diagnostics(self):
        excerpts = []
        paths = [path for path in self.logs if path.name.startswith("server-")]
        for path in dict.fromkeys([*paths, *self.logs[-3:]]):
            with path.open("rb") as log:
                log.seek(max(0, path.stat().st_size - LOG_LIMIT))
                excerpts.append(
                    f"{path.name}:\n{log.read(LOG_LIMIT).decode('utf-8', errors='replace')}"
                )
        return "\n".join(excerpts)

    @staticmethod
    def stop_server(process):
        # Descendants share this session, including multiprocessing workers.
        try:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"Server process group {process.pid} leaked after cleanup"
                    )
                time.sleep(0.05)

    def start_server(self, module, reservation, ready_url, ready_payload):
        path = self.root / f"server-{len(self.logs)}.log"
        self.logs.append(path)
        # The module binds its own socket; hold the reservation until spawn.
        with path.open("ab", buffering=0) as log:
            reservation.close()
            process = subprocess.Popen(
                [sys.executable, "-m", module],
                cwd=WORKSPACE,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.addCleanup(self.stop_server, process)
        deadline = time.monotonic() + 20
        last_error = "no response"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(
                    f"{module} exited ({process.returncode})\n{self.diagnostics()}"
                )
            try:
                response = self.client.get(ready_url, timeout=0.5)
                if response.status_code == 200 and response.json() == ready_payload:
                    return process
                last_error = f"{response.status_code}: {response.text[:LOG_LIMIT]}"
            except (httpx.HTTPError, ValueError) as error:
                last_error = str(error)
            time.sleep(0.05)
        self.fail(f"{module} readiness timed out: {last_error}\n{self.diagnostics()}")

    def run_python(self, *arguments):
        path = self.root / f"command-{len(self.logs)}.log"
        self.logs.append(path)
        with path.open("wb") as log:
            try:
                result = subprocess.run(
                    [sys.executable, *map(str, arguments)],
                    cwd=WORKSPACE,
                    env=self.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=75,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self.fail(f"Command timed out: {arguments}\n{self.diagnostics()}")
        self.assertEqual(result.returncode, 0, f"{arguments}\n{self.diagnostics()}")
        self.assertLessEqual(path.stat().st_size, 65536, self.diagnostics())
        return path.read_text(encoding="utf-8")

    def cli(self, *arguments, json_output=True):
        output = self.run_python(
            "-m", "reqlica.cli", *arguments, *(["--json"] if json_output else [])
        )
        if not json_output:
            return output
        try:
            return json.loads(output)
        except ValueError:
            self.fail(f"CLI returned invalid JSON\n{self.diagnostics()}")

    def request(self, method, url, status=200, **options):
        try:
            response = self.client.request(method, url, **options)
        except httpx.HTTPError as error:
            self.fail(f"{method} {url}: {error}\n{self.diagnostics()}")
        self.assertEqual(
            response.status_code,
            status,
            f"{method} {url}: {response.text[:LOG_LIMIT]}\n{self.diagnostics()}",
        )
        return response

    def live_rows(self, directory):
        database = (directory / "data.sqlite").as_uri() + "?mode=ro"
        with closing(sqlite3.connect(database, uri=True)) as connection:
            return [
                dict(zip(("id", "value"), row))
                for row in connection.execute(
                    "SELECT id, value FROM records ORDER BY id"
                )
            ]

    def test_cli_draft_activation_and_persistent_isolated_records(self):
        primary = self.root / "primary"
        secondary = self.root / "secondary"
        projects = []
        for directory in (primary, secondary):
            output = self.cli("project", "create", directory, json_output=False)
            metadata = json.loads(
                (directory / "project.json").read_text(encoding="utf-8")
            )
            self.assertIn(metadata["id"], output)
            projects.append(metadata)
            # Explicit fixture schema setup before any project worker is started.
            self.run_python(
                "-c",
                """
import sys
from reqlica.runtime.sdk import configure_database, connection, transaction
configure_database(sys.argv[1])
with transaction() as database:
    database.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    database.execute("INSERT INTO records (value) VALUES (?)", (sys.argv[2],))
with connection() as database:
    assert database.execute("SELECT value FROM records").fetchone()[0] == sys.argv[2]
""",
                directory / "data.sqlite",
                "baseline" if directory == primary else "other-baseline",
            )
        project, other = projects
        project_id, other_id = project["id"], other["id"]
        baseline = [{"id": 1, "value": "baseline"}]
        other_baseline = [{"id": 1, "value": "other-baseline"}]

        # Only the second project's fresh, never-started fixture bypasses publishing.
        other_code = secondary / "versions" / other["active_version"]
        (other_code / "endpoints" / "records.py").write_text(
            RECORD_SOURCE, encoding="utf-8"
        )
        manifest_path = other_code / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["endpoints"].extend(RECORD_ROUTES)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self.start_server(
            "reqlica.console.app",
            self.console_socket,
            self.console_url + "/api/health",
            {"status": "ok", "service": "reqlica-console"},
        )
        gateway = self.start_server(
            "reqlica.runtime.app",
            self.gateway_socket,
            self.gateway_url + "/mock/demo/hello",
            {"message": "Hello from Reqlica", "project_id": "demo"},
        )
        listing = self.cli("project", "list")["projects"]
        self.assertEqual({item["id"] for item in listing}, {project_id, other_id})
        self.assertTrue(all(item["status"] == "idle" for item in listing), listing)
        base = f"{self.gateway_url}/mock/{project_id}"
        other_base = f"{self.gateway_url}/mock/{other_id}"
        hello = {"message": "Hello from Reqlica", "project_id": project_id}
        self.assertEqual(self.request("GET", base + "/hello").json(), hello)
        active = self.cli("project", "endpoints", project_id)
        self.assertEqual(active["status"], "running")

        state = self.cli("draft", "create", project_id)
        draft_id = state["id"]
        starter = primary / "drafts" / draft_id / "tests" / "test_hello.py"
        starter_source = starter.read_text(encoding="utf-8")
        for relative, source in (
            ("endpoints/records.py", RECORD_SOURCE),
            ("tests/test_records.py", RECORD_TEST),
        ):
            fixture = self.root / Path(relative).name
            fixture.write_text(source, encoding="utf-8")
            previous = state["revision"]
            state = self.cli(
                "draft",
                "write",
                project_id,
                draft_id,
                relative,
                "--file",
                fixture,
                "--revision",
                previous,
            )
            self.assertNotEqual(state["revision"], previous)
        for route in RECORD_ROUTES:
            state = self.cli(
                "draft",
                "upsert",
                project_id,
                draft_id,
                route["method"],
                route["path"],
                route["handler"],
                "--revision",
                state["revision"],
            )
        revision = state["revision"]
        self.assertEqual(starter.read_text(encoding="utf-8"), starter_source)
        for command in ("test", "validate"):
            checked = self.cli(
                "draft", command, project_id, draft_id, "--revision", revision
            )
            self.assertEqual(checked["status"], "passed", str(checked)[:LOG_LIMIT])
            self.assertEqual(checked["revision"], revision)
            if command == "test":
                self.assertEqual(
                    (checked["tests_run"], checked["tests_passed"]), (2, 2)
                )
                self.assertIn("test_hello", checked["output"])
                self.assertIn("test_records", checked["output"])
            self.assertEqual(self.live_rows(primary), baseline)

        called = self.cli(
            "draft",
            "call",
            project_id,
            draft_id,
            "POST",
            "/records",
            "--body",
            json.dumps({"value": "draft-only"}),
            "--revision",
            revision,
        )
        self.assertEqual(called["status"], "passed", str(called)[:LOG_LIMIT])
        self.assertEqual(called["status_code"], 200)
        self.assertEqual(
            called["body"], {"id": 2, "value": "draft-only", "method": "POST"}
        )
        called = self.cli(
            "draft",
            "call",
            project_id,
            draft_id,
            "GET",
            "/records",
            "--revision",
            revision,
        )
        self.assertEqual(called["status_code"], 200)
        self.assertEqual(called["body"], baseline)
        self.assertEqual(self.live_rows(primary), baseline)
        self.assertEqual(self.request("GET", base + "/hello").json(), hello)
        self.request("GET", base + "/records", status=404)
        self.request("POST", base + "/records", status=404, json={"value": "not-live"})
        unchanged = self.cli("project", "endpoints", project_id)
        self.assertEqual(unchanged["pid"], active["pid"])
        self.assertEqual(unchanged["active_version"], project["active_version"])
        self.assertEqual(unchanged["endpoints"], active["endpoints"])

        approved = self.cli(
            "draft",
            "approve",
            project_id,
            draft_id,
            "--revision",
            revision,
            "--yes",
        )
        self.assertEqual(approved["approval"], {"revision": revision})
        published = self.cli(
            "draft", "activate", project_id, draft_id, "--revision", revision
        )
        self.assertEqual(published["status"], "running")
        self.assertNotEqual(published["active_version"], project["active_version"])
        self.assertEqual(published["url"], base)
        self.assertEqual(self.request("GET", base + "/hello").json(), hello)
        self.assertEqual(self.request("GET", base + "/records").json(), baseline)
        self.request("POST", base + "/records", status=422, json={"value": ""})
        created = self.request("POST", base + "/records", json={"value": "live"}).json()
        self.assertEqual(created, {"id": 2, "value": "live", "method": "POST"})
        saved = [*baseline, {"id": 2, "value": "live"}]
        failed = self.request(
            "POST", base + "/records/fail", status=500, json={"value": "rollback"}
        )
        self.assertEqual(failed.json(), {"detail": "fixture rollback"})
        self.assertEqual(self.request("GET", base + "/records").json(), saved)

        self.assertEqual(
            self.request("GET", other_base + "/records").json(), other_baseline
        )
        created = self.request(
            "POST", other_base + "/records", json={"value": "other-live"}
        ).json()
        self.assertEqual(created, {"id": 2, "value": "other-live", "method": "POST"})
        other_saved = [*other_baseline, {"id": 2, "value": "other-live"}]
        self.assertEqual(
            self.request("GET", other_base + "/records").json(), other_saved
        )
        self.assertEqual(self.request("GET", base + "/records").json(), saved)
        self.assertEqual(self.live_rows(primary), saved)
        self.assertEqual(self.live_rows(secondary), other_saved)

        stopped = self.cli("project", "stop", project_id)
        self.assertEqual((stopped["status"], stopped["pid"]), ("stopped", None))
        self.request("GET", base + "/records", status=503)
        self.assertEqual(
            self.request("GET", other_base + "/records").json(), other_saved
        )
        started = self.cli("project", "start", project_id)
        self.assertEqual(started["status"], "running")
        self.assertEqual(started["active_version"], published["active_version"])
        self.assertEqual(self.request("GET", base + "/records").json(), saved)

        self.stop_server(gateway)
        self.assertIsNotNone(gateway.returncode)
        gateway = self.start_server(
            "reqlica.runtime.app",
            self.reserve_port(self.gateway_port),
            self.gateway_url + "/mock/demo/hello",
            {"message": "Hello from Reqlica", "project_id": "demo"},
        )
        listing = self.cli("project", "list")["projects"]
        self.assertTrue(all(item["status"] == "idle" for item in listing), listing)
        self.assertEqual({item["id"] for item in listing}, {project_id, other_id})
        self.assertEqual(self.request("GET", base + "/records").json(), saved)
        self.assertEqual(
            self.request("GET", other_base + "/records").json(), other_saved
        )
        self.assertEqual(self.request("GET", base + "/hello").json(), hello)
        restarted = self.cli("project", "endpoints", project_id)
        self.assertEqual(restarted["active_version"], published["active_version"])
        self.assertEqual(restarted["endpoints"], published["endpoints"])
        created = self.request(
            "POST", base + "/records", json={"value": "after-restart"}
        ).json()
        self.assertEqual(created, {"id": 3, "value": "after-restart", "method": "POST"})
        self.assertEqual(
            self.request("GET", base + "/records").json(),
            [*saved, {"id": 3, "value": "after-restart"}],
        )
        self.assertEqual(
            self.request("GET", other_base + "/records").json(), other_saved
        )


if __name__ == "__main__":
    unittest.main()
