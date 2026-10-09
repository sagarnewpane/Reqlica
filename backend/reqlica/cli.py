"""Launch and supervise Reqlica's console and mock servers."""

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
from reqlica.projects.store import create_project


def main() -> int:
    parser = argparse.ArgumentParser(prog="reqlica")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", help="Start the console and mock servers")
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
    for command, help_text in (
        ("endpoints", "Show a project's endpoints"),
        ("start", "Start a project's worker"),
        ("stop", "Stop a project and disable its lazy restart"),
    ):
        command_parser = project_commands.add_parser(command, help=help_text)
        command_parser.add_argument(
            "project_id", help="32-character lowercase hex project ID"
        )
        command_parser.add_argument(
            "--json", action="store_true", help="Print the API JSON payload"
        )
    args = parser.parse_args()

    if args.command == "project":
        if args.project_command == "create":
            return _create_project(args.directory, args.name)
        return _manage_project(args)
    return _start_servers()


def _mock_base_url() -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    host = os.environ.get("MOCK_HOST", "127.0.0.1")
    host = {"0.0.0.0": "127.0.0.1", "::": "::1", "[::]": "::1"}.get(host, host)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = os.environ.get("MOCK_PORT", "4000")
    return f"http://{host}:{port}"


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
    method = "POST" if command in {"start", "stop"} else "GET"
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
