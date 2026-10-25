"""Build the static dashboard and stage it inside the Python package.

Run npm ci in frontend/ first. The packaged application does not need Node.js.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    destination = root / "backend" / "reqlica" / "static"
    subprocess.run(["npm", "run", "build"], cwd=frontend, check=True)
    output = frontend / "out"
    if not (output / "index.html").is_file() or (output / "api").exists():
        raise RuntimeError(
            "Expected a static dashboard export without server API routes"
        )
    with tempfile.TemporaryDirectory(
        prefix="reqlica-ui-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / "static"
        shutil.copytree(output, staged)
        if destination.is_symlink():
            raise RuntimeError("Refusing to replace a symlinked asset directory")
        if destination.exists():
            shutil.rmtree(destination)
        staged.rename(destination)
    print(f"Dashboard staged at {destination}")


if __name__ == "__main__":
    main()
