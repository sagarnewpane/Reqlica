"""Launch and supervise Reqlica's console and mock servers."""

import argparse
import signal
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(prog="reqlica")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", help="Start the console and mock servers")
    parser.parse_args()

    def stop(signum, frame):
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, stop)
    processes = []
    try:
        for name, module in (
            ("Console", "reqlica.console.app"),
            ("Mock server", "reqlica.runtime.app"),
        ):
            # Let the CLI coordinate shutdown instead of signaling each child.
            process = subprocess.Popen(
                [sys.executable, "-m", module], start_new_session=True
            )
            processes.append((name, process))

        print("Starting console and mock servers. Press Ctrl+C to stop.", flush=True)
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(
                        f"{name} exited unexpectedly (code {code}); stopping Reqlica.",
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
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
