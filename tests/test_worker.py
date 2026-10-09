"""Exercise the gateway against real spawn workers and temporary projects."""

import asyncio
import gzip
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from reqlica.projects.store import Manifest, Project, create_project, registry_directory
from reqlica.runtime.app import _filter_proxy_headers, app
from reqlica.runtime.worker import Worker, WorkerManager

HANDLERS = """
from __future__ import annotations
import gzip
import os
import time
import helper
from fastapi import Request, Response
from pydantic import BaseModel

helper.IMPORTS += 1

class Payload(BaseModel):
    value: int

def root():
    return {"pid": os.getpid(), "name": helper.NAME, "imports": helper.IMPORTS}

async def echo(request: Request):
    return {
        "method": request.method,
        "raw_path": request.scope["raw_path"].decode(),
        "query": request.scope["query_string"].decode(),
        "body": (await request.body()).hex(),
        "host": request.headers["host"],
        "cookie": request.headers.get("cookie"),
        "removed": request.headers.get("x-remove-request"),
    }

def typed(payload: Payload, response: Response):
    response.status_code = 201
    response.set_cookie("first", "one")
    response.set_cookie("second", "two")
    response.headers["connection"] = "X-Remove-Response"
    response.headers["x-remove-response"] = "secret"
    return {"value": payload.value}

def compressed():
    return Response(gzip.compress(b"compressed response"), headers={"content-encoding": "gzip"})

def head():
    return Response(b"head body", headers={"x-head": "yes"})

def options():
    return Response(status_code=204, headers={"allow": "GET, OPTIONS"})

def crash():
    os._exit(7)

def docs():
    return {"custom": True}
"""


class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        environment = patch.dict(
            os.environ, {"REQLICA_HOME": str(self.directory / "registry")}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def project(self, name="example", endpoints=None, source=HANDLERS):
        directory = self.directory / name
        directory.mkdir()
        project = create_project(directory, name=name)
        (directory / "endpoints").mkdir(exist_ok=True)
        (directory / "endpoints" / "hello.py").write_text(source)
        (directory / "helper.py").write_text(f"IMPORTS = 0\nNAME = {name!r}\n")
        if endpoints is None:
            endpoints = [
                ("GET", "/", "root"),
                ("POST", "/raw/{path:path}", "echo"),
                ("PUT", "/raw/{path:path}", "echo"),
                ("PATCH", "/raw/{path:path}", "echo"),
                ("DELETE", "/raw/{path:path}", "echo"),
                ("POST", "/typed", "typed"),
                ("GET", "/compressed", "compressed"),
                ("HEAD", "/head", "head"),
                ("OPTIONS", "/options", "options"),
                ("GET", "/slash/", "root"),
                ("GET", "/crash", "crash"),
                ("GET", "/docs", "docs"),
                ("GET", "/openapi.json", "docs"),
            ]
        manifest = {
            "endpoints": [
                {
                    "method": method,
                    "path": path,
                    "handler": f"endpoints/hello.py:{handler}",
                }
                for method, path, handler in endpoints
            ]
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return project

    def test_lazy_isolated_workers_reuse_modules_and_shutdown(self):
        first = self.project("first")
        second = self.project("second")
        gateway_modules = set(sys.modules)
        with TestClient(app) as client:
            manager = app.state.worker_manager
            self.assertEqual(manager.workers, {})
            first_response = client.get(f"/mock/{first.id}")
            self.assertEqual(first_response.status_code, 200, first_response.text)
            first_body = first_response.json()
            repeated = client.get(f"/mock/{first.id}/").json()
            second_body = client.get(f"/mock/{second.id}/").json()
            self.assertEqual(first_body, repeated)
            self.assertEqual(first_body["imports"], 1)
            self.assertEqual(first_body["name"], "first")
            self.assertEqual(second_body["name"], "second")
            self.assertNotEqual(first_body["pid"], os.getpid())
            self.assertNotEqual(first_body["pid"], second_body["pid"])
            self.assertFalse(
                any(
                    name.startswith("_reqlica_handler_")
                    for name in set(sys.modules) - gateway_modules
                )
            )
            pids = {first_body["pid"], second_body["pid"]}
        self.assertEqual(manager.workers, {})
        self.assertFalse(
            pids & {process.pid for process in multiprocessing.active_children()}
        )

    def test_scaffold_endpoint_survives_restart_and_loads_edits(self):
        project = create_project(self.directory / "scaffold")
        url = f"/mock/{project.id}/hello"
        with TestClient(app) as client:
            original = client.get(url)
            self.assertEqual(original.status_code, 200, original.text)
            self.assertEqual(
                original.json(),
                {"message": "Hello from Reqlica", "project_id": project.id},
            )
            (project.directory / "endpoints" / "hello.py").write_text(
                'def hello():\n    return {"edited": True}\n'
            )
            self.assertEqual(client.get(url).json(), original.json())
        with TestClient(app) as client:
            self.assertEqual(client.get(url).json(), {"edited": True})

    def test_discovery_lists_projects_and_urls_without_importing_handlers(self):
        project = self.project(
            endpoints=[("GET", "/books/{book_id:int}", "get_book")],
            source="raise RuntimeError('must not be imported by discovery')",
        )
        with TestClient(app, base_url="http://localhost:4100") as client:
            response = client.get("/api/projects")
            self.assertEqual(response.status_code, 200, response.text)
            info = response.json()["projects"][0]
            self.assertEqual(info["id"], project.id)
            self.assertEqual(info["name"], project.name)
            self.assertEqual(info["directory"], str(project.directory))
            self.assertEqual(info["status"], "idle")
            self.assertIsNone(info["pid"])
            self.assertIsNone(info["error"])
            self.assertEqual(info["url"], f"http://localhost:4100/mock/{project.id}")
            self.assertEqual(
                info["endpoints"],
                [
                    {
                        "method": "GET",
                        "path": "/books/{book_id:int}",
                        "handler": "endpoints/hello.py:get_book",
                        "url": info["url"] + "/books/{book_id}",
                    }
                ],
            )
            self.assertEqual(
                client.get(f"/api/projects/{project.id}/endpoints").json(), info
            )
            self.assertEqual(
                client.get("/api/projects?running=true").json(), {"projects": []}
            )
            self.assertEqual(app.state.worker_manager.workers, {})
            paths = client.get("/openapi.json").json()["paths"]
            self.assertIn("/api/projects", paths)
            self.assertIn("/api/projects/{project_id}/endpoints", paths)
            self.assertIn("post", paths["/api/projects/{project_id}/start"])
            self.assertIn("post", paths["/api/projects/{project_id}/stop"])

    def test_explicit_start_stop_is_idempotent_and_does_not_affect_other_workers(self):
        first = self.project("first")
        second = self.project("second")
        with TestClient(app) as client:
            start_url = f"/api/projects/{first.id}/start"
            stop_url = f"/api/projects/{first.id}/stop"
            started = client.post(start_url)
            self.assertEqual(started.status_code, 200, started.text)
            info = started.json()
            self.assertEqual(info["status"], "running")
            self.assertEqual(client.post(start_url).json()["pid"], info["pid"])
            self.assertEqual(
                client.get(f"/mock/{first.id}/").json()["pid"], info["pid"]
            )
            second_pid = client.get(f"/mock/{second.id}/").json()["pid"]
            running = client.get("/api/projects?running=true").json()["projects"]
            self.assertEqual(
                {project["id"] for project in running}, {first.id, second.id}
            )
            stopped = client.post(stop_url)
            self.assertEqual(stopped.status_code, 200, stopped.text)
            self.assertEqual(stopped.json()["status"], "stopped")
            self.assertIsNone(stopped.json()["pid"])
            self.assertEqual(client.post(stop_url).json(), stopped.json())
            self.assertNotIn(
                info["pid"], {p.pid for p in multiprocessing.active_children()}
            )
            blocked = client.get(f"/mock/{first.id}/")
            self.assertEqual(blocked.status_code, 503, blocked.text)
            self.assertIn("project start", blocked.json()["detail"])
            self.assertEqual(
                client.get(f"/mock/{second.id}/").json()["pid"], second_pid
            )
            running = client.get("/api/projects?running=true").json()["projects"]
            self.assertEqual([project["id"] for project in running], [second.id])
            restarted = client.post(start_url)
            self.assertEqual(restarted.status_code, 200, restarted.text)
            self.assertEqual(restarted.json()["status"], "running")
            self.assertNotEqual(restarted.json()["pid"], info["pid"])
            self.assertEqual(client.get(f"/mock/{first.id}/").status_code, 200)

    def test_stopping_idle_project_blocks_requests_until_start_and_resets_on_gateway_restart(
        self,
    ):
        project = create_project(self.directory / "scaffold")
        with TestClient(app) as client:
            stopped = client.post(f"/api/projects/{project.id}/stop")
            self.assertEqual(stopped.status_code, 200, stopped.text)
            self.assertEqual(stopped.json()["status"], "stopped")
            self.assertEqual(app.state.worker_manager.workers, {})
            self.assertEqual(client.get(f"/mock/{project.id}/hello").status_code, 503)
            self.assertEqual(
                client.get(f"/api/projects/{project.id}/endpoints").json()["status"],
                "stopped",
            )
        with TestClient(app) as client:
            self.assertEqual(
                client.get(f"/api/projects/{project.id}/endpoints").json()["status"],
                "idle",
            )
            self.assertEqual(client.get(f"/mock/{project.id}/hello").status_code, 200)

    def test_running_endpoints_are_loaded_snapshot_and_stop_start_loads_manifest_edits(
        self,
    ):
        project = self.project(endpoints=[("GET", "/", "root")])
        prefix = f"/api/projects/{project.id}"
        with TestClient(app) as client:
            original = client.post(prefix + "/start").json()
            (project.directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "endpoints": [
                            {
                                "method": "GET",
                                "path": "/new",
                                "handler": "endpoints/hello.py:root",
                            }
                        ]
                    }
                )
            )
            loaded = client.get(prefix + "/endpoints").json()
            self.assertEqual(loaded["endpoints"], original["endpoints"])
            self.assertEqual(
                client.post(prefix + "/start").json()["pid"], original["pid"]
            )
            self.assertEqual(client.get(f"/mock/{project.id}/new").status_code, 404)
            self.assertEqual(client.post(prefix + "/stop").status_code, 200)
            configured = client.get(prefix + "/endpoints").json()
            self.assertEqual(configured["endpoints"][0]["path"], "/new")
            restarted = client.post(prefix + "/start")
            self.assertEqual(restarted.status_code, 200, restarted.text)
            self.assertEqual(restarted.json()["endpoints"], configured["endpoints"])
            self.assertEqual(client.get(f"/mock/{project.id}/new").status_code, 200)
            self.assertEqual(client.get(f"/mock/{project.id}/").status_code, 404)

    def test_discovery_reports_bad_projects_without_hiding_healthy_projects(self):
        healthy = self.project("healthy")
        broken = self.project(
            "broken", source="raise RuntimeError('broken handler import')"
        )
        invalid_manifest = self.project("invalid-manifest")
        (invalid_manifest.directory / "manifest.json").write_text("not json")
        invalid_metadata = self.project("invalid-metadata")
        (invalid_metadata.directory / "project.json").write_text("not json")
        with TestClient(app) as client:
            healthy_pid = client.get(f"/mock/{healthy.id}/").json()["pid"]
            failed = client.post(f"/api/projects/{broken.id}/start")
            self.assertEqual(failed.status_code, 502, failed.text)
            info = client.get(f"/api/projects/{broken.id}/endpoints").json()
            self.assertEqual(info["status"], "error")
            self.assertIn("broken handler import", info["error"])
            listing = client.get("/api/projects")
            self.assertEqual(listing.status_code, 200, listing.text)
            projects = {
                project["id"]: project for project in listing.json()["projects"]
            }
            self.assertEqual(projects[healthy.id]["status"], "running")
            self.assertEqual(projects[healthy.id]["pid"], healthy_pid)
            for project in (broken, invalid_manifest, invalid_metadata):
                self.assertEqual(projects[project.id]["status"], "error")
                self.assertTrue(projects[project.id]["error"])
            for project in (invalid_manifest, invalid_metadata):
                response = client.get(f"/api/projects/{project.id}/endpoints")
                self.assertEqual(response.status_code, 422, response.text)
            for project_id in ("a" * 32, "invalid-id"):
                for method, suffix in (
                    ("GET", "endpoints"),
                    ("POST", "start"),
                    ("POST", "stop"),
                ):
                    response = client.request(
                        method, f"/api/projects/{project_id}/{suffix}"
                    )
                    self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(
                client.get(f"/mock/{healthy.id}/").json()["pid"], healthy_pid
            )

    def test_discovery_reaps_crashed_workers_and_start_recovers_them(self):
        project = self.project()
        with TestClient(app) as client:
            original = client.post(f"/api/projects/{project.id}/start").json()["pid"]
            self.assertEqual(client.get(f"/mock/{project.id}/crash").status_code, 502)
            info = client.get(f"/api/projects/{project.id}/endpoints")
            self.assertEqual(info.status_code, 200, info.text)
            self.assertEqual(info.json()["status"], "error")
            self.assertIsNone(info.json()["pid"])
            self.assertNotIn(project.id, app.state.worker_manager.workers)
            self.assertNotIn(
                original, {p.pid for p in multiprocessing.active_children()}
            )
            restarted = client.post(f"/api/projects/{project.id}/start")
            self.assertEqual(restarted.status_code, 200, restarted.text)
            self.assertEqual(restarted.json()["status"], "running")
            self.assertIsNone(restarted.json()["error"])

    def test_stop_succeeds_even_if_running_project_files_become_invalid(self):
        project = self.project()
        prefix = f"/api/projects/{project.id}"
        with TestClient(app) as client:
            started = client.post(prefix + "/start").json()
            (project.directory / "manifest.json").unlink()
            (project.directory / "project.json").write_text("not json")
            stopped = client.post(prefix + "/stop")
            self.assertEqual(stopped.status_code, 200, stopped.text)
            self.assertEqual(stopped.json()["status"], "stopped")
            self.assertIsNone(stopped.json()["pid"])
            self.assertEqual(stopped.json()["name"], project.name)
            self.assertEqual(stopped.json()["endpoints"], started["endpoints"])
            self.assertEqual(client.post(prefix + "/stop").json(), stopped.json())
            self.assertNotIn(
                started["pid"], {p.pid for p in multiprocessing.active_children()}
            )
            self.assertEqual(client.get(f"/mock/{project.id}/").status_code, 503)
            listed = client.get(prefix + "/endpoints")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["status"], "stopped")
            self.assertTrue(listed.json()["error"])

    def test_stop_invalid_idle_project_and_missing_manifest_discovery(self):
        project = self.project()
        prefix = f"/api/projects/{project.id}"
        (project.directory / "manifest.json").unlink()
        with TestClient(app) as client:
            self.assertEqual(client.get(prefix + "/endpoints").status_code, 422)
            stopped = client.post(prefix + "/stop")
            self.assertEqual(stopped.status_code, 200, stopped.text)
            self.assertEqual(stopped.json()["status"], "stopped")
            self.assertTrue(stopped.json()["error"])
            self.assertEqual(client.get(f"/mock/{project.id}/").status_code, 503)
            self.assertEqual(app.state.worker_manager.workers, {})
            self.assertEqual(client.post(prefix + "/start").status_code, 502)
            self.assertEqual(client.get(f"/mock/{project.id}/").status_code, 503)

    def test_concurrent_lifecycle_commands_return_their_own_state_snapshots(self):
        project = self.project()
        manager = WorkerManager()
        self.addCleanup(manager.close)

        def start():
            result = manager.start(project.id)
            self.assertEqual(result["status"], "running")
            self.assertIsNotNone(result["pid"])
            return result

        def stop():
            result = manager.stop(project.id)
            self.assertEqual(result["status"], "stopped")
            self.assertIsNone(result["pid"])
            return result

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(operation) for operation in (start, stop, start, stop)
            ]
            for future in futures:
                future.result()

    def test_stop_known_crashed_worker_after_registry_entry_is_deleted(self):
        project = self.project()
        with TestClient(app) as client:
            self.assertEqual(
                client.post(f"/api/projects/{project.id}/start").status_code, 200
            )
            self.assertEqual(client.get(f"/mock/{project.id}/crash").status_code, 502)
            (registry_directory() / f"{project.id}.json").unlink()
            stopped = client.post(f"/api/projects/{project.id}/stop")
            self.assertEqual(stopped.status_code, 200, stopped.text)
            self.assertEqual(stopped.json()["status"], "stopped")
            self.assertEqual(stopped.json()["name"], project.name)
            self.assertEqual(client.get(f"/mock/{project.id}/").status_code, 503)

    def test_start_reports_failure_if_worker_exits_immediately_after_readiness(self):
        project = create_project(self.directory / "scaffold")
        process = MagicMock()
        process.is_alive.return_value = False
        manifest = Manifest.model_validate_json(
            (project.directory / "manifest.json").read_text()
        )
        with TestClient(app) as client:
            with patch.object(
                app.state.worker_manager,
                "_start_worker",
                return_value=Worker(process, 4000, project, manifest),
            ):
                response = client.post(f"/api/projects/{project.id}/start")
            self.assertEqual(response.status_code, 502, response.text)
            self.assertIn("worker exited", response.json()["detail"])
            process.close.assert_called_once_with()

    def test_delayed_handler_is_not_cut_off_by_default_httpx_timeout(self):
        source = 'import asyncio\nasync def slow():\n    await asyncio.sleep(5.1)\n    return {"delayed": True}\n'
        project = self.project(endpoints=[("GET", "/slow", "slow")], source=source)
        with TestClient(app) as client:
            response = client.get(f"/mock/{project.id}/slow")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"delayed": True})

    def test_raw_requests_and_standard_fastapi_signatures(self):
        project = self.project()
        prefix = f"/mock/{project.id}"
        with TestClient(app, base_url="http://original.example:4321") as client:
            echo = client.post(
                prefix + "/raw/a%2Fb%20c?value=%2F&value=two+words",
                content=b"\x00raw\xff",
                headers={
                    "connection": "X-Remove-Request",
                    "x-remove-request": "secret",
                },
            )
            self.assertEqual(echo.status_code, 200, echo.text)
            self.assertEqual(
                echo.json(),
                {
                    "method": "POST",
                    "raw_path": prefix + "/raw/a%2Fb%20c",
                    "query": "value=%2F&value=two+words",
                    "body": b"\x00raw\xff".hex(),
                    "host": "original.example:4321",
                    "cookie": None,
                    "removed": None,
                },
            )
            for method in ("PUT", "PATCH", "DELETE"):
                response = client.request(
                    method, prefix + "/raw/example", content=b"body"
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["method"], method)
                self.assertEqual(response.json()["body"], b"body".hex())
            typed = client.post(prefix + "/typed", json={"value": 42})
            self.assertEqual(typed.status_code, 201)
            self.assertEqual(typed.json(), {"value": 42})
            self.assertEqual(len(typed.headers.get_list("set-cookie")), 2)
            self.assertNotIn("connection", typed.headers)
            self.assertNotIn("x-remove-response", typed.headers)
            client.cookies.clear()
            without_cookies = client.post(prefix + "/raw/example")
            self.assertIsNone(without_cookies.json()["cookie"])
            with_cookies = client.post(
                prefix + "/raw/example", headers={"cookie": "caller=only"}
            )
            self.assertEqual(with_cookies.json()["cookie"], "caller=only")
            self.assertEqual(
                client.post(prefix + "/typed", json={"value": "invalid"}).status_code,
                422,
            )

    def test_head_options_redirects_compression_and_custom_docs(self):
        project = self.project()
        prefix = f"/mock/{project.id}"
        with TestClient(app, base_url="https://original.example:4321") as client:
            head = client.head(prefix + "/head")
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.content, b"")
            self.assertEqual(head.headers["content-length"], str(len(b"head body")))
            self.assertEqual(head.headers["x-head"], "yes")
            options = client.options(prefix + "/options")
            self.assertEqual(options.status_code, 204)
            self.assertEqual(options.headers["allow"], "GET, OPTIONS")
            redirect = client.get(
                prefix + "/slash",
                follow_redirects=False,
                headers={"x-forwarded-proto": "http"},
            )
            self.assertEqual(redirect.status_code, 307)
            self.assertEqual(
                redirect.headers["location"],
                "https://original.example:4321" + prefix + "/slash/",
            )
            with client.stream("GET", prefix + "/compressed") as response:
                raw = b"".join(response.iter_raw())
                self.assertEqual(gzip.decompress(raw), b"compressed response")
                self.assertEqual(int(response.headers["content-length"]), len(raw))
            self.assertEqual(client.get(prefix + "/docs").json(), {"custom": True})
            self.assertEqual(
                client.get(prefix + "/openapi.json").json(), {"custom": True}
            )

    def test_bad_project_and_startup_failures_do_not_break_healthy_worker(self):
        healthy = self.project("healthy")
        invalid = self.project(
            "invalid", source="raise RuntimeError('broken handler import')"
        )
        missing = self.project("missing", endpoints=[("GET", "/", "absent")])
        malformed = self.project("malformed")
        (malformed.directory / "manifest.json").write_text("not json")
        no_manifest = self.project("no-manifest")
        (no_manifest.directory / "manifest.json").unlink()
        with TestClient(app) as client:
            self.assertEqual(client.get("/mock/not-registered/").status_code, 404)
            healthy_pid = client.get(f"/mock/{healthy.id}").json()["pid"]
            for project, message in [
                (invalid, "broken handler import"),
                (missing, "not callable"),
                (malformed, "validation error"),
                (no_manifest, "manifest.json"),
            ]:
                with self.subTest(project=project.name):
                    response = client.get(f"/mock/{project.id}")
                    self.assertEqual(response.status_code, 502, response.text)
                    self.assertIn(message, response.json()["detail"])
                    self.assertNotIn(project.id, app.state.worker_manager.workers)
            self.assertEqual(
                client.get(f"/mock/{healthy.id}").json()["pid"], healthy_pid
            )

    def test_rejects_symlink_escape_and_non_regular_handler(self):
        escaped = self.project("escaped")
        outside = self.directory / "outside.py"
        outside.write_text("raise RuntimeError('outside handler was imported')")
        handler = escaped.directory / "endpoints" / "hello.py"
        handler.unlink()
        handler.symlink_to(outside)
        not_file = self.project("not-file")
        handler = not_file.directory / "endpoints" / "hello.py"
        handler.unlink()
        handler.mkdir()
        with TestClient(app) as client:
            for project in (escaped, not_file):
                response = client.get(f"/mock/{project.id}")
                self.assertEqual(response.status_code, 502)
                self.assertIn(
                    "regular .py file inside the project", response.json()["detail"]
                )

    def test_crashed_worker_is_reaped_and_restarted(self):
        project = self.project()
        with TestClient(app) as client:
            original = client.get(f"/mock/{project.id}").json()["pid"]
            self.assertEqual(client.get(f"/mock/{project.id}/crash").status_code, 502)
            restarted = client.get(f"/mock/{project.id}")
            self.assertEqual(restarted.status_code, 200, restarted.text)
            self.assertNotEqual(restarted.json()["pid"], original)
            self.assertNotIn(
                original, {process.pid for process in multiprocessing.active_children()}
            )

    def test_concurrent_startup_creates_one_worker(self):
        project = self.project()
        manager = WorkerManager()
        self.addCleanup(manager.close)
        with ThreadPoolExecutor(max_workers=4) as pool:
            workers = list(pool.map(manager.get_or_start, [project.id] * 4))
        self.assertTrue(all(worker is workers[0] for worker in workers))
        self.assertEqual(len(manager.workers), 1)

    def test_startup_timeout_cleans_up_process(self):
        project = self.project(
            source="import time\ntime.sleep(60)\n"
            + HANDLERS.replace("from __future__ import annotations\n", "")
        )
        manager = WorkerManager(startup_timeout=0.2)
        before = {process.pid for process in multiprocessing.active_children()}
        try:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                manager.get_or_start(project.id)
            self.assertEqual(manager.workers, {})
            self.assertEqual(
                {process.pid for process in multiprocessing.active_children()}, before
            )
        finally:
            manager.close()

    def test_slow_startup_does_not_block_gateway_event_loop(self):
        project = self.project(
            source="import time\ntime.sleep(0.5)\n"
            + HANDLERS.replace("from __future__ import annotations\n", "")
        )

        async def check():
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://gateway"
                ) as client,
            ):
                slow = asyncio.create_task(client.get(f"/mock/{project.id}"))
                await asyncio.sleep(0.05)
                demo = await asyncio.wait_for(
                    client.get("/mock/demo/hello"), timeout=0.3
                )
                self.assertEqual(demo.status_code, 200)
                self.assertFalse(slow.done())
                self.assertEqual((await slow).status_code, 200)

        asyncio.run(check())


class HeaderTests(unittest.TestCase):
    def test_connection_nominated_headers_are_case_insensitive(self):
        self.assertEqual(
            _filter_proxy_headers(
                [
                    (b"Connection", b" X-Secret, KEEP-ALIVE "),
                    (b"x-secret", b"hidden"),
                    (b"Keep-Alive", b"timeout=5"),
                    (b"Transfer-Encoding", b"chunked"),
                    (b"Set-Cookie", b"a=1"),
                    (b"Set-Cookie", b"b=2"),
                ]
            ),
            [(b"Set-Cookie", b"a=1"), (b"Set-Cookie", b"b=2")],
        )


class WorkerShutdownTests(unittest.TestCase):
    def test_workers_share_one_shutdown_deadline(self):
        manager = WorkerManager()
        calls = MagicMock()
        for index in range(3):
            process = MagicMock()
            process.is_alive.return_value = True
            calls.attach_mock(process, f"worker{index}")
            manager.workers[str(index)] = Worker(
                process,
                4000 + index,
                Project(id=f"{index:032x}", name=str(index), directory=Path("/unused")),
            )
        processes = [worker.process for worker in manager.workers.values()]
        with patch(
            "reqlica.runtime.worker.time.monotonic", side_effect=[0, 0.25, 1, 2.5]
        ):
            manager.close()
        for process, timeout in zip(processes, (1.75, 1, 0)):
            process.terminate.assert_called_once_with()
            self.assertEqual(
                process.join.call_args_list[0].kwargs, {"timeout": timeout}
            )
            process.kill.assert_called_once_with()
            process.close.assert_called_once_with()
        operations = [call[0] for call in calls.mock_calls]
        self.assertLess(
            operations.index("worker2.terminate"), operations.index("worker0.join")
        )
        self.assertEqual(manager.workers, {})
