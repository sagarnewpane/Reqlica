"""Launch and supervise Reqlica's console and mock servers."""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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
    args = parser.parse_args()

    if args.command == "project":
        return _create_project(args.directory, args.name)
    return _start_servers()


def _create_project(directory: Path, name: str | None) -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        project = create_project(directory, name)
    except (OSError, ValueError) as error:
        print(f"Could not create project: {error}", file=sys.stderr)
        return 1

    host = os.environ.get("MOCK_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    port = os.environ.get("MOCK_PORT", "4000")

    print(f"Created {project.name} ({project.id}) at {project.directory}")
    print(f"Mock endpoint: http://{host}:{port}/mock/{project.id}/hello")
    print("Run 'reqlica start' to serve it, or call it if Reqlica is already running.")
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
