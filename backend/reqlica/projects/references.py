"""Resolve unambiguous human-friendly references without guessing a project/draft."""

import re

from reqlica.projects import drafts
from reqlica.projects.store import get_project, list_project_ids


def resolve_project_reference(reference: str) -> str:
    """Accept a full ID, unique >=6-character ID prefix, or exact display name."""
    if re.fullmatch(r"[0-9a-f]{32}", reference):
        return reference
    ids = list_project_ids()
    matches = set()
    if re.fullmatch(r"[0-9a-f]{6,31}", reference):
        matches.update(
            project_id for project_id in ids if project_id.startswith(reference)
        )
    for project_id in ids:
        try:
            project = get_project(project_id)
        except (OSError, ValueError):
            continue
        if project.name.casefold() == reference.casefold():
            matches.add(project_id)
    if not matches:
        raise ValueError(
            "Unknown project reference; use its exact name or at least 6 ID characters"
        )
    if len(matches) != 1:
        raise ValueError(
            "Ambiguous project reference; use a longer prefix or the full ID"
        )
    return matches.pop()


def resolve_draft_reference(project_id: str, reference: str) -> str:
    """Accept a full draft ID or an unambiguous >=6-character prefix in this project."""
    if re.fullmatch(r"[0-9a-f]{32}", reference):
        return reference
    if not re.fullmatch(r"[0-9a-f]{6,31}", reference):
        raise ValueError(
            "Draft reference must be a full ID or at least 6 ID characters"
        )
    project = get_project(project_id)
    directory = drafts._contained(project.directory, ".reqlica/drafts")
    matches = [
        path.stem
        for path in directory.glob("*.json")
        if re.fullmatch(r"[0-9a-f]{32}", path.stem) and path.stem.startswith(reference)
    ]
    if not matches:
        raise FileNotFoundError("Unknown draft reference in this project")
    if len(matches) != 1:
        raise ValueError(
            "Ambiguous draft reference; use a longer prefix or the full ID"
        )
    return matches[0]
