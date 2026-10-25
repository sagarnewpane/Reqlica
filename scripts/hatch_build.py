"""Include the built dashboard in release artifacts, not editable installs."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class DashboardBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return
        assets = Path(self.root) / "backend" / "reqlica" / "static"
        if (
            not (assets / "index.html").is_file()
            or not (assets / "project.html").is_file()
        ):
            raise RuntimeError(
                "Dashboard assets are missing. Install frontend dependencies with "
                "'npm --prefix frontend ci', then run 'python3 scripts/build_frontend.py' "
                "before building a release."
            )
        target = (
            "reqlica/static"
            if self.target_name == "wheel"
            else "backend/reqlica/static"
        )
        build_data["force_include"][str(assets)] = target
