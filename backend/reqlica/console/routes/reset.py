"""Explicit baseline capture and scenario counter reset; live restore uses gateway."""

from fastapi import APIRouter, Response
from reqlica.console.dependencies import draft_operation
from reqlica.projects.reset import ConfirmedRevision, baseline_info, capture_baseline
from reqlica.projects.scenarios import reset_scenario_counters

router = APIRouter(prefix="/api/projects/{project_id}", tags=["Reset"])


@router.get("/data-baseline")
def inspect_baseline(project_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(baseline_info, project_id)


@router.put("/data-baseline")
def capture(project_id: str, body: ConfirmedRevision, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(capture_baseline, project_id, body)


@router.post("/scenarios/reset")
def reset_scenarios(project_id: str, body: ConfirmedRevision, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return draft_operation(reset_scenario_counters, project_id, body.expected_revision)
