"""Shared MCP parameter descriptions for core and convenience tool groups."""

from typing import Annotated

from pydantic import Field

ProjectID = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "Existing project's ID from list_projects.projects[].id. "
            "Not a project name, directory, draft ID, or version ID."
        ),
    ),
]
DraftID = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "Unpublished draft's ID: use the 'id' returned by create_draft, "
            "or list_drafts.drafts[].id for existing work in this project. The LLM obtains this "
            "from tools; do not ask the user to invent an ID."
        ),
    ),
]
Revision = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Latest draft 'revision' returned by create_draft, a draft edit, "
            "list_drafts, or get_draft_changes. Use that exact value, never invent a hash. "
            "Edits must be sequential: each edit returns the revision for the next. "
            "On conflict, inspect changes and reread draft context before retrying."
        ),
    ),
]
RequestMethod = Annotated[
    str,
    Field(
        description=(
            "Uppercase HTTP method of the route, e.g. GET or POST. "
            "Supported: GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD, TRACE."
        ),
    ),
]
