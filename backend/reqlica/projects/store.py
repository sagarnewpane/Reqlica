"""Store file-based projects and their locations in a local registry."""

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.routing import compile_path


class ProjectMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be blank")
        return name


class Project(ProjectMetadata):
    directory: Path


class Endpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"]
    path: str
    handler: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError(
                "Endpoint path must start with / and contain no query or fragment"
            )
        try:
            compile_path(path)
        except (AssertionError, ValueError) as exc:
            raise ValueError(f"Invalid endpoint path: {exc}") from exc
        return path

    @field_validator("handler")
    @classmethod
    def validate_handler(cls, handler: str) -> str:
        if handler.count(":") != 1:
            raise ValueError("Handler must use relative/file.py:function format")
        filename, function_name = handler.split(":")
        path = PurePosixPath(filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in filename
            or path.suffix != ".py"
            or not function_name.isidentifier()
        ):
            raise ValueError(
                "Handler must name a project-relative .py file and a function"
            )
        return handler


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoints: list[Endpoint]

    @model_validator(mode="after")
    def unique_routes(self) -> "Manifest":
        seen_routes = set()
        for endpoint in self.endpoints:
            # Ignore capture names so /books/{id} conflicts with /books/{name:str}.
            path_regex, _, path_parameters = compile_path(endpoint.path)
            route_pattern = path_regex.pattern
            for parameter_name in path_parameters:
                route_pattern = route_pattern.replace(f"(?P<{parameter_name}>", "(?:")
            route_key = (endpoint.method, route_pattern)
            if route_key in seen_routes:
                raise ValueError(f"Duplicate route: {endpoint.method} {endpoint.path}")
            seen_routes.add(route_key)
        return self


def registry_directory() -> Path:
    home = os.environ.get("REQLICA_HOME", "~/.reqlica")
    return Path(home).expanduser().resolve() / "projects"


def list_project_ids() -> list[str]:
    """List registered IDs without importing handlers or hiding broken projects."""
    return sorted(
        entry.stem
        for entry in registry_directory().glob("*.json")
        if entry.is_file() and re.fullmatch(r"[0-9a-f]{32}", entry.stem)
    )


def create_project(directory: Path, name: str | None = None) -> Project:
    """Create an empty-directory project with a working, manually editable endpoint."""
    directory = Path(directory).expanduser().resolve()
    project = Project(
        id=uuid4().hex,
        name=name if name is not None else directory.name,
        directory=directory,
    )
    if directory.exists() and any(directory.iterdir()):
        raise ValueError(f"Project directory must be empty: {directory}")

    hello_source = f'''"""A sample endpoint. Add standard FastAPI parameters as needed."""

def hello() -> dict[str, str]:
    return {{
        "message": "Hello from Reqlica",
        "project_id": "{project.id}",
    }}
'''
    manifest = Manifest(
        endpoints=[
            Endpoint(method="GET", path="/hello", handler="endpoints/hello.py:hello")
        ]
    )
    project_files = {
        "endpoints/hello.py": hello_source,
        "manifest.json": manifest.model_dump_json(indent=2) + "\n",
        "project.json": project.model_dump_json(indent=2, exclude={"directory"}) + "\n",
    }
    registry = registry_directory()
    registry.mkdir(parents=True, exist_ok=True)
    registry_entry = registry / f"{project.id}.json"
    temporary_entry = registry / f".{project.id}.{uuid4().hex}.tmp"
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        # Check registry access before creating files, then publish the entry last.
        with temporary_entry.open("x", encoding="utf-8") as file:
            file.write(json.dumps({"directory": str(directory)}) + "\n")
        directory_existed = directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        if not directory_existed:
            created_directories.append(directory)
        endpoints_directory = directory / "endpoints"
        endpoints_directory.mkdir()
        created_directories.append(endpoints_directory)

        for filename, content in project_files.items():
            path = directory / filename
            with path.open("x", encoding="utf-8") as file:
                created_files.append(path)
                file.write(content)
        temporary_entry.replace(registry_entry)
    except BaseException:
        # Roll back only this invocation's scaffolding; preserve existing contents.
        for path in reversed(created_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for path in reversed(created_directories):
            try:
                path.rmdir()
            except OSError:
                pass
        raise
    finally:
        try:
            temporary_entry.unlink(missing_ok=True)
        except OSError:
            pass
    return project


def get_project(project_id: str) -> Project:
    """Resolve a registered ID and read the project's current metadata."""
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise FileNotFoundError(f"Unknown project: {project_id}")
    registry_entry = registry_directory() / f"{project_id}.json"
    if not registry_entry.is_file():
        raise FileNotFoundError(f"Unknown project: {project_id}")
    try:
        location = json.loads(registry_entry.read_text(encoding="utf-8"))
        directory = Path(location["directory"])
        if not directory.is_absolute():
            raise ValueError("Registered directory must be absolute")
        metadata_file = directory / "project.json"
        metadata = ProjectMetadata.model_validate_json(
            metadata_file.read_text(encoding="utf-8")
        )
        if metadata.id != project_id:
            raise ValueError("Registered ID does not match project.json")
        return Project(
            id=metadata.id, name=metadata.name, directory=directory.resolve()
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Cannot open project {project_id}: {exc}") from exc
