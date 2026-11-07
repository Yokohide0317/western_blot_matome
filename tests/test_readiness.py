from wb import api
from wb.readiness import output_readiness, REASON_TEXT
from test_rendering import small_project


def test_readiness_rules_and_japanese_status(small_project):
    root, project = small_project
    readiness = output_readiness(project)
    assert not readiness["final_allowed"]
    reasons = {b["reason"] for b in readiness["blocking_items"]}
    assert {
        "region_assignment_unconfirmed",
        "selected_exposure_missing",
        "display_geometry_unset",
        "lane_count_unset",
    } <= reasons
    status = api.status(root)
    assert set(status) == {"project_revision", "panels", "unresolved_items", "excluded_image_sets", "warnings", "readiness"}
    assert status["warnings"] == []
    for blocker in status["readiness"]["blocking_items"]:
        assert blocker["text"] == REASON_TEXT[blocker["reason"]]


def test_exports_do_not_require_historical_review_status(small_project):
    from pathlib import Path
    root, _ = small_project
    assert Path(api.export_project(root, "pptx")["path"]).is_file()


def test_no_panels_are_not_ready(small_project):
    _, project = small_project
    project.panels = []
    assert output_readiness(project) == {
        "final_allowed": False,
        "blocking_items": [{"panel_id": None, "reason": "no_panels"}],
    }


def test_excluded_panels_do_not_block_readiness(small_project):
    _, project = small_project
    project.panels[0].included = False
    blockers = output_readiness(project)["blocking_items"]
    assert blockers and all(b["panel_id"] == project.panels[1].panel_id for b in blockers)
    project.panels[1].included = False
    assert output_readiness(project)["blocking_items"] == [{"panel_id": None, "reason": "no_panels"}]
