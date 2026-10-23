"""Supervise servers, manage projects/drafts, and record local human approval."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from reqlica.projects import drafts
from reqlica.projects.references import (
    resolve_draft_reference,
    resolve_project_reference,
)
from reqlica.projects.store import create_project
from reqlica.projects.urls import get_draft_urls


def main() -> int:
    parser = argparse.ArgumentParser(prog="reqlica")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", help="Start the console and mock servers")
    demo = commands.add_parser("demo", help="Create a stateful bookstore API demo")
    demo.add_argument("directory", type=Path)
    projects = commands.add_parser("project", help="Manage local mock projects")
    project_commands = projects.add_subparsers(dest="project_command", required=True)
    create = project_commands.add_parser(
        "create", help="Create a project with a sample /hello endpoint"
    )
    create.add_argument("directory", type=Path, help="New or empty project directory")
    create.add_argument(
        "--name", help="Project display name (defaults to the directory name)"
    )
    listing = project_commands.add_parser("list", help="List registered projects")
    listing.add_argument(
        "--running", action="store_true", help="Show only running projects"
    )
    listing.add_argument(
        "--json", action="store_true", help="Print the API JSON payload"
    )
    deletion = project_commands.add_parser(
        "delete", help="Stop and unregister a project; keep its files unless requested"
    )
    deletion.add_argument(
        "project", help="Project name, full ID, or unique 6+ character ID prefix"
    )
    deletion.add_argument(
        "--delete-files",
        action="store_true",
        help="Permanently delete the project folder, database, and drafts",
    )
    deletion.add_argument(
        "--yes", action="store_true", help="Confirm without prompting"
    )
    deletion.add_argument(
        "--json", action="store_true", help="Print the deletion result as JSON"
    )
    for command, help_text in (
        ("endpoints", "Show a project's endpoints"),
        ("start", "Start a project's worker"),
        ("stop", "Stop a project and disable its lazy restart"),
        ("convert", "Convert a stopped or idle legacy project to versions"),
    ):
        command_parser = project_commands.add_parser(command, help=help_text)
        command_parser.add_argument(
            "project_id", help="32-character lowercase hex project ID"
        )
        command_parser.add_argument(
            "--json", action="store_true", help="Print the API JSON payload"
        )
    draft_parser = commands.add_parser("draft", help="Edit and check unpublished code")
    draft_commands = draft_parser.add_subparsers(dest="draft_command", required=True)
    for command in (
        "create",
        "list",
        "inspect",
        "write",
        "delete",
        "upsert",
        "remove",
        "validate",
        "test",
        "call",
        "url",
        "approve",
        "activate",
    ):
        command_parser = draft_commands.add_parser(command)
        command_parser.add_argument("project_id")
        if command not in {"create", "list"}:
            command_parser.add_argument("draft_id")
        if command not in {"create", "list", "inspect", "url"}:
            command_parser.add_argument(
                "--revision",
                required=command not in {"validate", "test", "call"},
                help="Exact revision; checks/calls inspect the current revision if omitted",
            )
        if command in {"validate", "test", "call", "url"}:
            command_parser.description = (
                "Accepts a project name or unique ID prefix (6+ characters), "
                "and a full draft ID or unique draft prefix (6+ characters)."
            )
        if command == "url":
            command_parser.add_argument(
                "path",
                nargs="?",
                help="Registered route pattern; omit to list all URLs",
            )
        if command in {"write", "delete"}:
            command_parser.add_argument("path", help="Draft-relative file path")
        if command == "write":
            command_parser.add_argument("--file", type=Path, required=True)
        if command in {"upsert", "remove", "call"}:
            command_parser.add_argument("method")
            command_parser.add_argument("path", help="Endpoint route path")
        if command == "upsert":
            command_parser.add_argument("handler", help="relative/file.py:function")
        if command == "call":
            command_parser.add_argument(
                "--body", type=json.loads, help="JSON request body"
            )
        if command == "approve":
            command_parser.add_argument(
                "--yes",
                action="store_true",
                help="Explicitly approve without prompting",
            )
        command_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "demo":
        from reqlica.demo import create_demo
        try:
            project = create_demo(args.directory)
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(project.model_dump(mode="json"), indent=2))
        return 0

    if args.command == "project":
        if args.project_command == "create":
            return _create_project(args.directory, args.name)
        if args.project_command == "delete":
            return _delete_project(args)
        return _manage_project(args)
    if args.command == "draft":
        return _manage_draft(args)
    return _start_servers()


def _mock_base_url() -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    host = os.environ.get("MOCK_HOST", "127.0.0.1")
    host = {"0.0.0.0": "127.0.0.1", "::": "::1", "[::]": "::1"}.get(host, host)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = os.environ.get("MOCK_PORT", "4000")
    return f"http://{host}:{port}"


def _console_base_url() -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    host = os.environ.get("CONSOLE_HOST", "127.0.0.1")
    host = {"0.0.0.0": "127.0.0.1", "::": "::1", "[::]": "::1"}.get(host, host)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{os.environ.get('CONSOLE_PORT', '3000')}"


def _manage_draft(args: argparse.Namespace) -> int:
    command = args.draft_command
    if command in {"validate", "test", "call", "url"}:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        try:
            args.project_id = resolve_project_reference(args.project_id)
            args.draft_id = resolve_draft_reference(args.project_id, args.draft_id)
        except (OSError, ValueError) as error:
            print(f"Could not resolve draft: {error}", file=sys.stderr)
            return 1
    for field, length in (("project_id", 32), ("draft_id", 32), ("revision", 64)):
        value = getattr(args, field, None)
        if value is not None and not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
            print(
                f"Invalid {field}: expected {length} lowercase hex characters.",
                file=sys.stderr,
            )
            return 1
    try:
        if command == "url":
            payload = get_draft_urls(
                args.project_id, args.draft_id, _console_base_url(), args.path
            )
        elif command == "approve":
            # This intentionally has no HTTP equivalent or agent-facing operation.
            load_dotenv(Path(__file__).resolve().parents[2] / ".env")
            state = drafts.inspect_draft(args.project_id, args.draft_id)
            if state["revision"] != args.revision:
                raise ValueError("Draft revision conflict; inspect the current draft")
            if not args.yes:
                print(
                    json.dumps(
                        {
                            key: state[key]
                            for key in (
                                "id",
                                "revision",
                                "changes",
                                "validation",
                                "test",
                            )
                        },
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                print(
                    f"Approve exact revision {args.revision}? [y/N] ",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    accepted = input().strip().lower() in {"y", "yes"}
                except EOFError:
                    accepted = False
                if not accepted:
                    print("Approval declined; no approval recorded.", file=sys.stderr)
                    return 1
            payload = drafts.approve_draft(
                args.project_id, args.draft_id, args.revision
            )
        else:
            route = f"/api/projects/{args.project_id}/drafts"
            method = "GET" if command in {"list", "inspect"} else "POST"
            body = None
            options = {}
            if command not in {"create", "list"}:
                route += f"/{args.draft_id}"
            if command not in {"create", "list", "inspect"}:
                if command in {"validate", "test", "call"} and args.revision is None:
                    args.revision = _current_draft_revision(
                        args.project_id, args.draft_id
                    )
                body = {"expected_revision": args.revision}
                if command in {"write", "delete"}:
                    route += "/files"
                    options["params"] = {"path": args.path}
                    method = "PUT" if command == "write" else "DELETE"
                    if command == "write":
                        body["content"] = args.file.read_text(encoding="utf-8")
                elif command in {"upsert", "remove"}:
                    route += "/endpoints"
                    method = "PUT" if command == "upsert" else "DELETE"
                    endpoint = {"method": args.method, "path": args.path}
                    if command == "upsert":
                        body["endpoint"] = {**endpoint, "handler": args.handler}
                    else:
                        body.update(endpoint)
                elif command == "activate":
                    route = f"/api/projects/{args.project_id}/activate"
                    body["draft_id"] = args.draft_id
                else:
                    route += f"/{command}"
                    if command == "call":
                        body.update(
                            method=args.method, path=args.path, json_body=args.body
                        )
            base_url = (
                _mock_base_url() if command == "activate" else _console_base_url()
            )
            response = httpx.request(
                method,
                base_url + route,
                json=body,
                trust_env=False,
                timeout=60.0,
                **options,
            )
            if not response.is_success:
                try:
                    error_payload = response.json()
                    detail = (
                        error_payload.get("detail", error_payload)
                        if isinstance(error_payload, dict)
                        else error_payload
                    )
                except ValueError:
                    detail = response.text or "No error detail returned"
                print(
                    f"Draft {command} error ({response.status_code}): {detail}",
                    file=sys.stderr,
                )
                return 1
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Malformed API reply: expected a JSON object")
    except (OSError, ValueError, httpx.HTTPError, httpx.InvalidURL) as error:
        print(f"Could not {command} draft: {error}", file=sys.stderr)
        return 1
    if command == "url" and not args.json:
        for endpoint in payload["endpoints"]:
            print(f"{endpoint['method']} {endpoint['url']}")
        return 0
    if not args.json:
        print(f"Draft {command}:")
    print(json.dumps(payload, indent=2))
    if command in {"validate", "test", "call"} and payload.get("status") != "passed":
        print(
            f"Draft {command} did not pass: {payload.get('status', 'missing status')}",
            file=sys.stderr,
        )
        return 1
    return 0


def _current_draft_revision(project_id: str, draft_id: str) -> str:
    """Resolve once, then pass the exact hash to preserve normal conflict checks."""
    response = httpx.request(
        "GET",
        f"{_console_base_url()}/api/projects/{project_id}/drafts/{draft_id}",
        trust_env=False,
        timeout=60.0,
    )
    if not response.is_success:
        raise ValueError(
            f"Could not inspect draft ({response.status_code}): {response.text}"
        )
    state = response.json()
    revision = state.get("revision") if isinstance(state, dict) else None
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ValueError("Malformed draft reply: expected a 64-character revision")
    return revision


def _create_project(directory: Path, name: str | None) -> int:
    base_url = _mock_base_url()
    try:
        project = create_project(directory, name)
    except (OSError, ValueError) as error:
        print(f"Could not create project: {error}", file=sys.stderr)
        return 1

    print(f"Created {project.name} ({project.id}) at {project.directory}")
    print(f"Mock endpoint: {base_url}/mock/{project.id}/hello")
    print("Run 'reqlica start' to serve it, or call it if Reqlica is already running.")
    return 0


def _deletion_request(method: str, url: str, **options) -> dict:
    response = httpx.request(method, url, trust_env=False, timeout=60.0, **options)
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError(
            f"Gateway returned non-JSON ({response.status_code})"
        ) from error
    if not response.is_success:
        detail = (
            payload.get("detail", payload) if isinstance(payload, dict) else payload
        )
        raise ValueError(f"Gateway error ({response.status_code}): {detail}")
    if not isinstance(payload, dict):
        raise TypeError("Malformed deletion reply: expected a JSON object")
    return payload


def _delete_project(args: argparse.Namespace) -> int:
    """Require gateway-owned cleanup; never silently fall back to offline deletion."""
    try:
        base = _mock_base_url() + "/api/projects"
        project_id = args.project
        if not re.fullmatch(r"[0-9a-f]{32}", project_id):
            listing = _deletion_request("GET", base).get("projects")
            if not isinstance(listing, list) or any(
                not isinstance(item, dict) for item in listing
            ):
                raise ValueError("Malformed project listing")
            matches = {
                item["id"]
                for item in listing
                if isinstance(item.get("id"), str)
                and re.fullmatch(r"[0-9a-f]{32}", item["id"])
                and (
                    isinstance(item.get("name"), str)
                    and item["name"].casefold() == args.project.casefold()
                    or re.fullmatch(r"[0-9a-f]{6,31}", args.project)
                    and item["id"].startswith(args.project)
                )
            }
            if len(matches) != 1:
                raise ValueError(
                    "Unknown or ambiguous project; use its full ID or a longer unique prefix"
                )
            project_id = matches.pop()
        route = base + "/" + project_id
        info = _deletion_request(
            "GET", route + "/deletion", params={"delete_files": args.delete_files}
        )
        if info.get("id") != project_id or not isinstance(info.get("registered"), bool):
            raise ValueError("Malformed deletion info")
        directory = info.get("directory")
        if args.delete_files and (
            not isinstance(directory, str) or not Path(directory).is_absolute()
        ):
            raise ValueError(
                "Gateway did not return a confirmed absolute project directory"
            )
        if not args.yes:
            print(json.dumps(info, indent=2), file=sys.stderr)
            action = (
                "Permanently delete the folder and all project data"
                if args.delete_files
                else "Unregister the project and keep its files"
            )
            print(f"{action}? [y/N] ", end="", file=sys.stderr, flush=True)
            try:
                accepted = input().strip().lower() in {"y", "yes"}
            except EOFError:
                accepted = False
            if not accepted:
                print("Deletion declined; no changes made.", file=sys.stderr)
                return 1
        payload = _deletion_request(
            "DELETE",
            route,
            json={
                "delete_files": args.delete_files,
                "expected_directory": directory if args.delete_files else None,
            },
        )
        if (
            payload.get("id") != project_id
            or payload.get("status") != "deleted"
            or payload.get("files_deleted") is not args.delete_files
        ):
            raise ValueError(
                "Malformed deletion result; inspect project state before retrying"
            )
    except (OSError, ValueError, TypeError, httpx.HTTPError, httpx.InvalidURL) as error:
        print(
            f"Could not delete project: {error}. Ensure 'reqlica start' is running with the current code.",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        action = (
            "Deleted project files and registration"
            if args.delete_files
            else "Unregistered project (files kept)"
        )
        print(f"{action}: {payload.get('name') or project_id} ({project_id})")
    return 0


def _manage_project(args: argparse.Namespace) -> int:
    command = args.project_command
    if command != "list" and not re.fullmatch(r"[0-9a-f]{32}", args.project_id):
        print(
            "Invalid project ID: expected 32 lowercase hexadecimal characters.",
            file=sys.stderr,
        )
        return 1

    route = "/api/projects"
    if command == "list":
        if args.running:
            route += "?running=true"
    else:
        route += f"/{args.project_id}/{command}"
    method = "POST" if command in {"start", "stop", "convert"} else "GET"
    try:
        response = httpx.request(
            method, _mock_base_url() + route, trust_env=False, timeout=30.0
        )
    except (httpx.HTTPError, OSError) as error:
        print(
            f"Could not reach the mock gateway: {error}. Run 'reqlica start' first "
            "and check MOCK_HOST/MOCK_PORT.",
            file=sys.stderr,
        )
        return 1
    except (ValueError, httpx.InvalidURL) as error:
        print(f"Invalid mock gateway configuration: {error}", file=sys.stderr)
        return 1

    if not response.is_success:
        detail = "No JSON error detail returned"
        try:
            error_payload = response.json()
            if isinstance(error_payload, dict) and "detail" in error_payload:
                detail = error_payload["detail"]
        except ValueError:
            pass
        print(f"Mock gateway error ({response.status_code}): {detail}", file=sys.stderr)
        if response.status_code == 404:
            print(
                "Check the project ID. If the gateway is an older version, restart "
                "'reqlica start' to load project management routes.",
                file=sys.stderr,
            )
        return 1

    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("expected a JSON object")
        projects = payload["projects"] if command == "list" else [payload]
        if not isinstance(projects, list):
            raise TypeError("expected a projects array")
        for project in projects:
            if not isinstance(project, dict):
                raise TypeError("expected a project object")
            if not isinstance(project["id"], str) or not re.fullmatch(
                r"[0-9a-f]{32}", project["id"]
            ):
                raise ValueError("invalid project ID")
            for field in ("name", "directory", "error"):
                if project[field] is not None and not isinstance(project[field], str):
                    raise ValueError(f"invalid project {field}")
            if project["status"] not in ("idle", "running", "stopped", "error"):
                raise ValueError("invalid project status")
            if project["pid"] is not None and type(project["pid"]) is not int:
                raise ValueError("invalid project PID")
            if not isinstance(project["endpoints"], list):
                raise TypeError("expected an endpoints array")
            for item in [project, *project["endpoints"]]:
                if not isinstance(item, dict) or not isinstance(item["url"], str):
                    raise TypeError("expected a full URL")
                url = httpx.URL(item["url"])
                if not url.is_absolute_url or url.scheme not in {"http", "https"}:
                    raise ValueError("expected a full HTTP URL")
            for endpoint in project["endpoints"]:
                if any(
                    not isinstance(endpoint[field], str)
                    for field in ("method", "path", "handler")
                ):
                    raise ValueError("invalid endpoint fields")
    except (ValueError, KeyError, TypeError, httpx.InvalidURL) as error:
        print(f"Malformed mock gateway reply: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2))
    elif not projects:
        print("No running projects." if args.running else "No projects registered.")
    else:
        for project in projects:
            print(
                f"{project['name'] or '(unnamed)'} ({project['id']}) "
                f"[{project['status']}]"
            )
            print(f"  Base URL: {project['url']}")
            if project["directory"] is not None:
                print(f"  Directory: {project['directory']}")
            if project["pid"] is not None:
                print(f"  PID: {project['pid']}")
            if project["error"] is not None:
                print(f"  Error: {project['error']}")
            for endpoint in project["endpoints"]:
                print(
                    f"  {endpoint['method']} {endpoint['url']} ({endpoint['handler']})"
                )
            if not project["endpoints"]:
                print("  No endpoints.")
    return 0


def _start_servers() -> int:
    """Run both servers until interrupted or either server exits."""

    def handle_shutdown(signum, frame):
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, handle_shutdown)
    servers = []
    try:
        for name, module in (
            ("Console", "reqlica.console.app"),
            ("Mock server", "reqlica.runtime.app"),
        ):
            # Let the CLI coordinate shutdown instead of signaling each child.
            process = subprocess.Popen(
                [sys.executable, "-m", module], start_new_session=True
            )
            servers.append((name, process))

        print("Starting console and mock servers. Press Ctrl+C to stop.", flush=True)
        while True:
            for name, process in servers:
                exit_code = process.poll()
                if exit_code is not None:
                    print(
                        f"{name} exited unexpectedly (code {exit_code}); stopping Reqlica.",
                        file=sys.stderr,
                    )
                    return 1
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Stopping Reqlica.", flush=True)
        return 0
    except OSError as error:
        print(f"Could not start Reqlica: {error}", file=sys.stderr)
        return 1
    finally:
        for _, process in servers:
            if process.poll() is None:
                process.terminate()
        for _, process in servers:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            finally:
                if os.name == "posix":
                    # Reap descendants even if their server crashed or was killed.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
