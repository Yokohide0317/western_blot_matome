"""save_panel_edit writes one project revision, or nothing."""

import json

import pytest
from fastapi.testclient import TestClient

from wb import api, project as P
from wb.app import create_app
from test_rendering import small_project

CROP = {"x0": 10, "y0": 10, "x1": 35, "y1": 25}
BAD_CROP = {"x0": -1, "y0": 10, "x1": 35, "y1": 25}


def edit(crop=CROP, **overrides):
    data = {
        "region": {
            "image_set_id": "SET",
            "reference_frame_id": "F1",
            "polygon": [[5, 5], [40, 5], [40, 30]],
            "lane_count": 3,
            "assignment_status": "confirmed",
        },
        "display_geometry": {
            "reference_frame_id": "F1",
            "rotation_deg": 2,
            "rotation_center": [25, 20],
            "crop_rect": crop,
            "target_width_px": 60,
            "lane_layout": {"lane_count": 3, "centers_x": [15, 22, 29], "status": "confirmed"},
        },
        "annotations": [
            {
                "frame_id": "F1",
                "lane_id": 1,
                "purpose": "band",
                "geometry": {"type": "point", "coordinates": [[20, 15]]},
                "created_by": "human",
                "band_status": "detected",
                "review_status": "draft",
            }
        ],
        "selected_exposure_id": "C2",
    }
    data.update(overrides)
    return data


def raw(path):
    return (P.locate(path)).read_bytes()


def test_one_edit_is_one_revision(small_project):
    path, p = small_project
    saved = api.save_panel_edit(path, "PANEL_K", p.project_revision, **edit())

    on_disk = json.loads(raw(path))
    assert saved.project_revision == p.project_revision + 1
    assert on_disk["project_revision"] == p.project_revision + 1
    assert len(on_disk["history"]) == len(p.history) + 1
    assert on_disk["history"][-1]["operation"] == "save_panel_edit"

    panel = next(x for x in on_disk["panels"] if x["panel_id"] == "PANEL_K")
    region = next(x for x in on_disk["membrane_regions"] if x["region_id"] == panel["region_id"])
    assert region["lane_count"] == 3 and region["geometry"]["frame_id"] == "F1"
    assert panel["display_geometry"]["geometry_revision"] == 1
    assert panel["display_geometry"]["crop_rect"] == CROP
    assert panel["selected_exposure_id"] == "C2"
    assert len(on_disk["annotations"]) == 1 and on_disk["annotations"][0]["panel_id"] == "PANEL_K"
    assert panel["band_annotation_ids"] == [on_disk["annotations"][0]["annotation_id"]]
    # the other panel is untouched
    other = next(x for x in on_disk["panels"] if x["panel_id"] == "PANEL_L")
    assert other["display_geometry"] is None and other["selected_exposure_id"] is None


def test_same_result_as_the_separate_operations(small_project, tmp_path_factory):
    path, p = small_project
    data = edit()
    combined = api.save_panel_edit(path, "PANEL_K", p.project_revision, **data)

    other = tmp_path_factory.mktemp("separate")
    for name in ("1.tif", "2.tif", "3.tif"):
        (other / name).write_bytes((path / name).read_bytes())
    copy = P.save_project(p, P.locate(other, create=True), p.project_revision, "copy")
    q = api.update_region(other, "REGION_K", **data["region"], expected_revision=copy.project_revision)
    q = api.update_display_geometry(other, "PANEL_K", **data["display_geometry"], expected_revision=q.project_revision)
    q = api.add_annotation(other, {**data["annotations"][0], "panel_id": "PANEL_K"}, q.project_revision)
    q = api.set_selected_exposure(other, "PANEL_K", "C2", q.project_revision)

    strip = {"project_revision", "history"}
    assert combined.model_dump(exclude=strip) == q.model_dump(exclude=strip)
    assert q.project_revision == copy.project_revision + 4  # the old UI needed four revisions


@pytest.mark.parametrize(
    "bad",
    [
        {"crop": BAD_CROP},
        {
            "annotations": [
                {
                    "frame_id": "NOPE",
                    "purpose": "band",
                    "geometry": {"type": "point", "coordinates": [[1, 1]]},
                    "created_by": "human",
                }
            ]
        },
        {
            "annotations": [
                {
                    "panel_id": "PANEL_L",
                    "frame_id": "F1",
                    "purpose": "band",
                    "geometry": {"type": "point", "coordinates": [[1, 1]]},
                    "created_by": "human",
                }
            ]
        },
        {"selected_exposure_id": "C_NOT_THERE"},
    ],
)
def test_any_invalid_part_leaves_project_json_untouched(small_project, bad):
    path, p = small_project
    before = raw(path)
    crop = bad.pop("crop", CROP)
    with pytest.raises(ValueError):
        api.save_panel_edit(path, "PANEL_K", p.project_revision, **edit(crop, **bad))
    assert raw(path) == before
    assert not list(path.glob(".*.wblot.*.tmp"))


def test_stale_revision_and_empty_edit_change_nothing(small_project):
    path, p = small_project
    before = raw(path)
    with pytest.raises(P.RevisionConflict):
        api.save_panel_edit(path, "PANEL_K", p.project_revision + 5, **edit())
    with pytest.raises(ValueError, match="保存する変更がありません"):
        api.save_panel_edit(path, "PANEL_K", p.project_revision)
    with pytest.raises(P.UnknownId):
        api.save_panel_edit(path, "PANEL_X", p.project_revision, **edit())
    assert raw(path) == before


def test_http_endpoint_is_one_revision_or_422_with_no_change(small_project):
    path, p = small_project
    client = TestClient(create_app(path))
    before = raw(path)

    bad = client.post(
        "/api/save_panel_edit", json={"panel_id": "PANEL_K", "expected_revision": p.project_revision, **edit(BAD_CROP)}
    )
    assert bad.status_code == 422
    assert raw(path) == before

    ok = client.post(
        "/api/save_panel_edit", json={"panel_id": "PANEL_K", "expected_revision": p.project_revision, **edit()}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["project_revision"] == p.project_revision + 1
    assert json.loads(raw(path))["project_revision"] == p.project_revision + 1

    stale = client.post(
        "/api/save_panel_edit", json={"panel_id": "PANEL_K", "expected_revision": p.project_revision, **edit()}
    )
    assert stale.status_code == 409
