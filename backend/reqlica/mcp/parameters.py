"""Shared MCP parameter descriptions for core and convenience tool groups."""

from typing import Annotated

from pydantic import Field

ProjectID = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "Project ID from list_projects.projects[].id; not a name, directory, draft or version ID."
        ),
    ),
]
DraftID = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "This project's unpublished draft ID from create_draft.id or "
            "list_drafts.drafts[].id. Obtain from tools; never ask the user to invent it."
        ),
    ),
]
Revision = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Exact latest revision from create_draft, an edit, list_drafts or "
            "get_draft_changes; never invent it. Edit sequentially using each returned "
            "revision. On conflict, inspect changes and reread draft context before retrying."
        ),
    ),
]
RequestMethod = Annotated[
    str,
    Field(
        description=(
            "Uppercase HTTP method: GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD or TRACE."
        ),
    ),
]
