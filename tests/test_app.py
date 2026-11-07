from pathlib import Path
import numpy as np
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest
from wb import api, project as P
from wb.app import create_app
from test_rendering import small_project

SEED = Path(__file__).resolve().parents[1] / "instructions/western_blot_project_seed.json"


def post(client, name, project, **data):
    response = client.post("/api/" + name, json={"expected_revision": project["project_revision"], **data})
    assert response.status_code == 200, response.text
    return response.json()


def test_http_human_path(input_zip, tmp_path):
    p = api.import_project(input_zip, tmp_path, seed_path=SEED)
    client = TestClient(create_app(tmp_path))
    p = client.get("/api/project").json()
    image_set = next(s for s in p["image_sets"] if s["image_set_id"] == "S05")
    capture = next(c for c in p["captures"] if c["capture_id"] == image_set["capture_ids"][0])
    frame = next(f for f in p["frames"] if f["source_asset_id"] == capture["presentation_source_asset_id"])
    fid = frame["frame_id"]
    p = post(
        client, "confirm_identity_alignment", p, image_set_id="S05", reference_frame_id=fid, evidence="synthetic test"
    )
    for label in ("K", "L"):
        p = post(
            client,
            "update_region",
            p,
            region_id="REGION_" + label,
            image_set_id="S05",
            reference_frame_id=fid,
            polygon=[[400, 400], [900, 400], [900, 700], [400, 700]],
            lane_count=6,
            assignment_status="confirmed",
        )
        fit = client.post("/api/fit_lanes", json={"lane_count": 6, "known": {"1": 450, "6": 850}}).json()
        p = post(
            client,
            "update_display_geometry",
            p,
            panel_id="PANEL_" + label,
            reference_frame_id=fid,
            rotation_deg=3,
            rotation_center=[650, 550],
            crop_rect={"x0": fit["crop_x"][0], "x1": fit["crop_x"][1], "y0": 400, "y1": 700},
            target_width_px=300,
            lane_layout={"lane_count": 6, "centers_x": fit["centers"], "status": "confirmed"},
        )
        for purpose, points, kind in [
            ("band", [[500, 500]], "point"),
            ("background_roi", [[500, 550], [520, 550], [520, 570]], "polygon"),
        ]:
            p = post(
                client,
                "add_annotation",
                p,
                annotation={
                    "frame_id": fid,
                    "panel_id": "PANEL_" + label,
                    "lane_id": 1,
                    "purpose": purpose,
                    "geometry": {"type": kind, "coordinates": points},
                    "created_by": "human",
                    "band_status": "detected",
                },
            )
        ids = [a["annotation_id"] for a in p["annotations"] if a["panel_id"] == "PANEL_" + label]
        p = post(client, "confirm_annotations", p, annotation_ids=ids)
    import cv2

    sizes = []
    for cid in image_set["capture_ids"]:
        resp = client.get(f"/api/image/panel/PANEL_K/{cid}.png")
        assert resp.status_code == 200, resp.text
        sizes.append(cv2.imdecode(np.frombuffer(resp.content, np.uint8), 1).shape)
    assert len(set(sizes)) == 1 and len(sizes) == 20
    assert next(x for x in p["panels"] if x["panel_id"] == "PANEL_K")["display_geometry"] is not None
    stale = client.post(
        "/api/set_selected_exposure",
        json={
            "panel_id": "PANEL_K",
            "capture_id": image_set["capture_ids"][0],
            "expected_revision": p["project_revision"] - 1,
        },
    )
    assert stale.status_code == 409 and stale.json()["detail"]["current_revision"] == p["project_revision"]
    bad = client.post(
        "/api/update_display_geometry",
        json={
            "panel_id": "PANEL_K",
            "reference_frame_id": fid,
            "rotation_deg": 0,
            "rotation_center": [0, 0],
            "crop_rect": {"x0": -1, "x1": 10, "y0": 0, "y1": 10},
            "target_width_px": 10,
            "lane_layout": {},
            "expected_revision": p["project_revision"],
        },
    )
    assert bad.status_code == 422


def test_metadata_and_landmarks(small_project, tmp_path):
    path, p = small_project
    book = Workbook()
    sheet = book.active
    sheet.title = "Panels"
    sheet.append(["membrane_label", "label", "lane_count", "sample_group_id"])
    sheet.append(["K", "TargetC", 6, "G1"])
    file = tmp_path / "metadata.xlsx"
    book.save(file)
    p = api.import_metadata(path, file, p.project_revision)
    group = next(g for g in p.sample_groups if g.sample_group_id == "G1")
    assert len(group.samples) == 6 and all(s.name is None for s in group.samples)
    region = next(r for r in p.membrane_regions if r.user_label == "K")
    region.target_assignment_status = "confirmed"
    region.target_label = "TargetC"
    p = api.P.save_project(p, api.P.locate(path), p.project_revision, "test")
    sheet.cell(2, 2, "other")
    book.save(file)
    with pytest.raises(ValueError, match="矛盾"):
        api.import_metadata(path, file, p.project_revision)
    points = np.array([[100.0, 200.0], [330.0, 210.0], [180.0, 420.0]])
    matrix = np.array([[1.1, -0.2, 30.0], [0.2, 1.1, -15.0], [0, 0, 1.0]])
    target = points @ matrix[:2, :2].T + matrix[:2, 2]
    fit = api.fit_marker_landmarks(points.tolist(), target.tolist())
    actual = points @ np.array(fit["matrix"])[:2, :2].T + np.array(fit["matrix"])[:2, 2]
    assert np.max(np.abs(actual - target)) < 1e-6


def test_marker_registration(small_project):
    path, p = small_project
    p = api.update_region(
        path, "REGION_K", "SET", "F1", [[5, 5], [40, 5], [40, 30]], 3, "confirmed", p.project_revision
    )
    marker = [[5, 5], [30, 5], [10, 25]]
    target = [[6.2, 7.5], [28.7, 10], [8.7, 26]]
    fit = api.fit_marker_landmarks(marker, target)
    p = api.add_marker_registration(
        path,
        "REGION_K",
        "F2",
        fit["matrix"],
        [[1, 1], [45, 1], [45, 35]],
        "confirmed",
        "landmarks checked",
        p.project_revision,
    )
    reg = p.marker_registrations[0]
    transform = next(t for t in p.transforms if t.transform_id == reg.transform_id)
    assert transform.from_frame_id == "F2" and transform.to_frame_id == "F1"
    mapped = np.array(marker) @ np.array(transform.matrix)[:2, :2].T + np.array(transform.matrix)[:2, 2]
    assert np.max(np.abs(mapped - target)) < 1e-6


def test_common_scripts_are_served_for_the_annotation_ui(small_project):
    from wb.app import WEB_DIR

    COMMON_DIR = WEB_DIR / "common"
    COMMON_FILES = ("frameview.js", "labels.js")
    path, _ = small_project
    client = TestClient(create_app(path))
    for name in COMMON_FILES:
        response = client.get("/common/" + name)
        assert response.status_code == 200
        assert (COMMON_DIR / name).read_text(encoding="utf-8") in response.text
    page = client.get("/legacy/").text
    assert page == client.get("/").text


def test_current_http_contract(small_project, monkeypatch):
    import sys
    import types

    root, project = small_project
    client = TestClient(create_app(root))
    assert client.get("/api/revision").json() == {"project_revision": project.project_revision}
    assert client.get("/api/status").json() == api.status(root)
    layout = {"units": "inches", "pages": []}
    monkeypatch.setitem(sys.modules, "wb.layout", types.SimpleNamespace(compute_layout=lambda p: layout))
    assert client.get("/api/layout").json() == layout
    image = client.get("/api/image/frame/F1.png?max_width=20")
    assert image.status_code == 200
    import cv2

    assert cv2.imdecode(np.frombuffer(image.content, np.uint8), 1).shape[:2] == (16, 20)
    assert (P.cache_dir(root) / "overviews/F1--w20.png").is_file()
    assert not (root / "wblot_output/cache").exists()
    assert client.get("/api/image/frame/F1.png?max_width=0").status_code == 422
    payload = {"panel_id": "PANEL_K", "label": "A", "expected_revision": project.project_revision}
    renamed = client.post("/api/rename_panel", json=payload)
    assert renamed.status_code == 200
    assert renamed.json()["panels"][0]["label"] == "A"
    conflict = client.post("/api/rename_panel", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["project_revision"] == project.project_revision + 1
    revision = renamed.json()["project_revision"]
    assert client.post('/api/confirm_panel', json={}).status_code == 404
    result = client.post('/api/export', json={'kind': 'pptx'})
    assert result.status_code == 200
    assert 'final' not in result.json()


def test_new_operations_http_validation_and_conflict(small_project):
    root, project = small_project
    client = TestClient(create_app(root))
    revision = project.project_revision
    bad = client.post('/api/approve_all', json={'expected_revision': revision, 'evidence': ''})
    assert bad.status_code == 404
    bad = client.post('/api/set_lanes', json={'expected_revision': revision, 'lane_count': True})
    assert bad.status_code == 422
    missing = client.post('/api/set_panel_included', json={'expected_revision': revision, 'panel_id': 'missing', 'included': False})
    assert missing.status_code == 422
    saved = post(client, 'set_panel_included', {'project_revision': revision}, panel_id='PANEL_L', included=False)
    assert not next(p for p in saved['panels'] if p['panel_id'] == 'PANEL_L')['included']
    assert client.post('/api/reorder_panels', json={'expected_revision': revision, 'panel_ids': ['PANEL_K']}).status_code == 409
    saved = post(client, 'reorder_panels', saved, panel_ids=['PANEL_L'])
    saved = post(client, 'set_lanes', saved, lane_count=2, sample_names=['a', 'b'])
    saved = post(client, 'duplicate_panel', saved, panel_id='PANEL_K')
    assert client.post('/api/approve_panel', json={}).status_code == 404
    assert client.get('/legacy/').status_code == 200
