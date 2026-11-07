import json
from pathlib import Path
import copy

from fastapi.testclient import TestClient

from wb.app import create_app
from wb import project as P

import pytest

from wb import api
from wb.readiness import output_readiness
from test_annotate_ui import running_app
from playwright.sync_api import sync_playwright


@pytest.fixture
def proposal():
    return json.loads((Path(__file__).parent / "fixtures/apply/proposal.json").read_text())


@pytest.fixture
def imported(tmp_path, input_zip):
    return tmp_path, api.import_project(input_zip, tmp_path)


def test_apply_and_confirm(imported, proposal):
    path, initial = imported
    project = api.apply_proposal(path, proposal, initial.project_revision)
    assert project.project_revision == initial.project_revision + 1
    assert len(project.membrane_regions) == 2 and len(project.panels) == 2
    assert all(
        r.region_assignment_status == "proposed" and r.created_by == "algorithm" for r in project.membrane_regions
    )
    assert all(
        p.review_status == "proposed" and p.display_geometry.lane_layout.lane_count is None for p in project.panels
    )
    assert all(t.status == "candidate" for t in project.transforms)
    assert [p.label for p in project.panels] == ["A", "B"]
    assert len({p.region_id for p in project.panels}) == 2
    for region, panel, membrane in zip(project.membrane_regions, project.panels, proposal["groups"][0]["membranes"]):
        assert [c.model_dump() for c in region.band_row_candidates] == membrane["row_candidates"]
        assert panel.selected_band_row_candidate_id == membrane["row"]["candidate_id"]
        assert panel.exposure_reason == membrane["row"]["exposure_reason"]
    from wb.rendering import render_panel

    assert all(render_panel(project, path, p.panel_id, p.selected_exposure_id).png for p in project.panels)
    assert project.marker_registrations[0].marker_roi.frame_id == "FRAME_IMG050"
    assert project.marker_registrations[1].marker_roi is not None
    assert project.marker_registrations[1].status == "candidate"
    assert project.proposal_diagnostics["excluded_image_sets"][0]["image_set_id"] == "S01"
    assert api.apply_proposal(path, proposal, project.project_revision).project_revision == project.project_revision
    reasons = {b["reason"] for b in output_readiness(project)["blocking_items"]}
    assert {
        "panel_proposed",
        "exposure_transform_candidate",
        "rectification_transform_candidate",
        "marker_transform_candidate",
    } <= reasons
    project = api.confirm_panel(path, project.panels[0].panel_id, project.project_revision, "目視確認")
    changed = json.loads(json.dumps(proposal))
    changed["warnings"].append("another")
    with pytest.raises(ValueError, match="確定済み"):
        api.apply_proposal(path, changed, project.project_revision)
    assert api._load(path).project_revision == project.project_revision
    panel = project.panels[0]
    project = api.rename_panel(path, panel.panel_id, "TargetA", project.project_revision)
    assert project.panels[0].label == "TargetA"
    for transform in list(project.transforms):
        project = api.confirm_transform(path, transform.transform_id, "overlay checked", project.project_revision)
    reasons = {b["reason"] for b in output_readiness(project)["blocking_items"]}
    candidates = {"exposure_transform_candidate", "rectification_transform_candidate", "marker_transform_candidate"}
    assert not candidates & reasons
    for region in list(project.membrane_regions):
        project = api.update_region(
            path,
            region.region_id,
            region.image_set_id,
            region.reference_frame_id,
            region.geometry.polygon,
            3,
            "confirmed",
            project.project_revision,
        )
    for panel in list(project.panels):
        project = api.confirm_panel(path, panel.panel_id, project.project_revision, "目視確認")
    project = api.add_marker_registration(
        path,
        "REGION_M2",
        "FRAME_IMG050",
        [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[530, 660], [600, 660], [600, 975], [530, 975]],
        "confirmed",
        "checked",
        project.project_revision,
    )
    assert output_readiness(project)["final_allowed"]


def test_proposal_ui(imported, proposal, monkeypatch):
    path, initial = imported
    monkeypatch.setattr(api, "propose", lambda project_dir: proposal)
    with running_app(path) as url, sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        api.apply_proposal(path, proposal, initial.project_revision)
        page.goto(url)
        page.get_by_role('button', name='次へ', exact=True).click()
        page.get_by_role('button', name='角度を右へ回す', exact=True).click()
        page.get_by_text('編集を保存しました', exact=True).wait_for()
        project = api._load(path)
        assert project.project_revision == initial.project_revision + 2
        assert project.panels[0].display_geometry.geometry_revision == 2
        browser.close()


def test_select_band_row_changes_only_vertical_crop_and_exposure(imported, proposal):
    path, initial = imported
    project = api.apply_proposal(path, proposal, initial.project_revision)
    panel = project.panels[0]
    crop = panel.display_geometry.crop_rect.model_dump()
    crop.update(x0=670, x1=1480)
    project = api.update_display_geometry(
        path, panel.panel_id, "FRAME_IMG030", 2, [1030, 650], crop, 500, {}, project.project_revision
    )
    project = api.confirm_panel(path, panel.panel_id, project.project_revision, "目視確認")
    before = project.model_copy(deep=True)
    candidate = project.membrane_regions[0].band_row_candidates[1]
    project = api.select_band_row(path, panel.panel_id, candidate.candidate_id, project.project_revision)
    panel = project.panels[0]
    assert project.project_revision == before.project_revision + 1
    assert project.transforms == before.transforms
    assert project.membrane_regions == before.membrane_regions
    assert project.panels[1] == before.panels[1]
    expected = before.panels[0].model_dump()
    expected["display_geometry"]["crop_rect"].update(
        y0=candidate.crop_rect_rectified.y0, y1=candidate.crop_rect_rectified.y1
    )
    expected["display_geometry"]["geometry_revision"] += 1
    expected.update(
        selected_band_row_candidate_id=candidate.candidate_id,
        selected_exposure_id=candidate.selected_exposure_id,
        exposure_reason=candidate.exposure_reason,
        review_status="proposed",
    )
    assert panel.model_dump() == expected
    assert project.history[-1].operation == "select_band_row"
    assert "human selected" in project.history[-1].summary
    assert not output_readiness(project)["final_allowed"]
    assert api.apply_proposal(path, proposal, project.project_revision) == project


def test_candidate_switch_endpoint_is_removed(imported, proposal):
    path, initial = imported
    project = api.apply_proposal(path, proposal, initial.project_revision)
    before = P.locate(path).read_bytes()
    client = TestClient(create_app(path))
    assert client.post('/api/select_band_row', json={'panel_id':project.panels[0].panel_id}).status_code == 404
    assert P.locate(path).read_bytes() == before


def test_apply_only_accepts_format_three(imported, proposal):
    path, project = imported
    proposal["proposal_format"] = 2
    with pytest.raises(ValueError, match="3である必要"):
        api.apply_proposal(path, proposal, project.project_revision)
    assert api._load(path) == project


def test_confirmed_region_is_preserved_until_explicit_replace(imported, proposal):
    path, initial = imported
    project = api.apply_proposal(path, proposal, initial.project_revision)
    region = project.membrane_regions[0]
    project = api.update_region(
        path,
        region.region_id,
        region.image_set_id,
        region.reference_frame_id,
        region.geometry.polygon,
        3,
        "confirmed",
        project.project_revision,
    )
    changed = copy.deepcopy(proposal)
    changed["warnings"].append("changed")
    before = (P.locate(path)).read_bytes()
    with pytest.raises(ValueError, match="確定済み"):
        api.apply_proposal(path, changed, project.project_revision)
    assert (P.locate(path)).read_bytes() == before
    replaced = api.apply_proposal(path, changed, project.project_revision, replace_existing=True)
    assert replaced.project_revision == project.project_revision + 1
    assert len(replaced.panels) == 2
    assert replaced.membrane_regions[0].region_assignment_status == "proposed"


def test_invalid_chosen_row_is_atomic(imported, proposal):
    path, initial = imported
    proposal["groups"][0]["membranes"][0]["row"]["candidate_id"] = "missing"
    before = (P.locate(path)).read_bytes()
    with pytest.raises(ValueError, match="採用する段"):
        api.apply_proposal(path, proposal, initial.project_revision)
    assert (P.locate(path)).read_bytes() == before


def test_candidate_switch_can_select_another_registered_series(imported, proposal):
    from wb.rendering import frame_for_capture

    path, initial = imported
    group = proposal["groups"][0]
    group["image_set_ids"].append("S04")
    other_series = next(s for s in initial.image_sets if s.image_set_id == "S04")
    capture_id = other_series.capture_ids[0]
    frame_id = frame_for_capture(initial, capture_id).frame_id
    group["transforms"].append(
        {
            "kind": "exposure_to_reference",
            "from_frame_id": frame_id,
            "to_frame_id": "FRAME_IMG030",
            "matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "method": "fixture",
            "score": {},
        }
    )
    candidate = group["membranes"][0]["row_candidates"][1]
    candidate["selected_exposure_id"] = capture_id
    project = api.apply_proposal(path, proposal, initial.project_revision)
    project = api.select_band_row(
        path, project.panels[0].panel_id, candidate["candidate_id"], project.project_revision
    )
    assert project.panels[0].image_set_id == "S04"
    assert project.panels[0].selected_exposure_id == capture_id
    from wb.rendering import render_panel

    assert render_panel(project, path, project.panels[0].panel_id, capture_id).png
