"""Synthetic HTTP-level tests for fixed-size band quantification (no normalisation).

Everything here is generated in a temporary folder: a small synthetic 16-bit
grayscale TIFF, fake sample names, and a project built from ingest records.
"""

import csv
import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
import tifffile
from fastapi.testclient import TestClient

from wb import api
from wb import project as P
from wb.app import create_app
from wb.model import DisplayGeometry, LaneLayout
from test_rendering import small_project  # noqa: F401 - pytest fixture

WIDTH, HEIGHT = 96, 64
ROI_W, ROI_H = 10, 6

# lane -> (band x, band y, band value); lane 2 also has a few saturated pixels.
LANE_PIXELS = {
    1: (6, 24, 1100),
    2: (36, 24, 1200),
    3: (66, 24, 80),
}
SATURATED_PIXELS = {(25, 38), (25, 39), (26, 38), (26, 39)}
BACKGROUND_VALUE = 100
LANES = [
    {"lane_id": 1, "x": 6, "y": 24, "background_x": 6, "background_y": 4, "excluded": False},
    {"lane_id": 2, "x": 36, "y": 24, "background_x": 36, "background_y": 4, "excluded": False},
    {"lane_id": 3, "x": 66, "y": 24, "background_x": 66, "background_y": 4, "excluded": False},
]
SAMPLE_NAMES = ["=1+1", "Sample B", "-2+3"]


def post(client, name, project, **data):
    response = client.post("/api/" + name, json={"expected_revision": project["project_revision"], **data})
    assert response.status_code == 200, response.text
    return response.json()


def start(client, project, **extra):
    project = post(client, "start_quantification", project, panel_id="PANEL_K", **extra)
    return project, project["quantification_groups"][-1]


def group_of(project, group_id=None):
    if group_id is None:
        return project["quantification_groups"][0]
    return next(g for g in project["quantification_groups"] if g["group_id"] == group_id)


def configure(client, project, group_id, **extra):
    data = {"group_id": group_id, "source_confirmed": True, "width_px": ROI_W, "height_px": ROI_H}
    data.update(extra)
    return post(client, "update_quantification", project, **data)


def compute(client, project, group_id):
    return post(client, "compute_quantification", project, group_id=group_id)


def revision(client):
    return client.get("/api/revision").json()["project_revision"]


def synthetic_pixels():
    pixels = np.full((HEIGHT, WIDTH), BACKGROUND_VALUE, dtype=np.uint16)
    for x, y, value in LANE_PIXELS.values():
        pixels[y : y + ROI_H, x : x + ROI_W] = value
    for y, x in SATURATED_PIXELS:
        pixels[y, x] = 65535
    return pixels


def expected_sum(pixels, lane):
    band = pixels[lane["y"] : lane["y"] + ROI_H, lane["x"] : lane["x"] + ROI_W].astype(np.float64)
    bg = pixels[lane["background_y"] : lane["background_y"] + ROI_H, lane["background_x"] : lane["background_x"] + ROI_W].astype(np.float64)
    return float(band.sum()), float(bg.mean())


@pytest.fixture
def quant_project(tmp_path):
    return build_project(tmp_path, synthetic_pixels())


def build_project(tmp_path, pixels):
    source_path = tmp_path / "synthetic.raw16.tif"
    tifffile.imwrite(source_path, pixels, photometric="miniswhite")
    raw = source_path.read_bytes()
    records = {
        "sources": [
            {
                "source_asset_id": "RAW1",
                "relative_path": source_path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "representation_kind": "analysis_export_candidate",
                "image": {
                    "width": WIDTH,
                    "height": HEIGHT,
                    "dtype": str(pixels.dtype),
                    "channels": "GRAY",
                    "page_count": 1,
                },
                "quant_eligibility": "unverified",
            }
        ],
        "frames": [
            {
                "frame_id": "F1",
                "source_asset_id": "RAW1",
                "width": WIDTH,
                "height": HEIGHT,
                "page_index": 0,
                "channel_selection": "GRAY",
            }
        ],
        "captures": [
            {
                "capture_id": "C1",
                "presentation_source_asset_id": "RAW1",
                "analysis_source_asset_id": "RAW1",
                "modality": "chemiluminescence",
                "exposure_s": 1.0,
            },
            {
                "capture_id": "C2",
                "presentation_source_asset_id": "RAW1",
                "analysis_source_asset_id": "RAW1",
                "modality": "chemiluminescence",
                "exposure_s": 2.0,
            },
        ],
        "image_sets": [{"image_set_id": "SET", "capture_ids": ["C1", "C2"], "reference_frame_id": "F1"}],
    }
    project = P.new_project("synthetic-quantification", records)
    project = P.add_region(project, "K", "TargetA")
    panel = next(p for p in project.panels if p.panel_id == "PANEL_K")
    panel.selected_exposure_id = "C1"
    panel.image_set_id = "SET"
    region = next(r for r in project.membrane_regions if r.region_id == "REGION_K")
    region.image_set_id = "SET"
    region.reference_frame_id = "F1"
    region.lane_count = 3
    project = P.save_project(project, P.locate(tmp_path, create=True), project.project_revision, "test_fixture")
    client = TestClient(create_app(tmp_path))
    data = client.get("/api/project").json()
    data = post(client, "set_lanes", data, panel_id="PANEL_K", lane_count=3, sample_names=SAMPLE_NAMES)
    return tmp_path, client, data, pixels, source_path


def test_start_creates_group_with_shared_size_and_defaults(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)

    assert group["panel_id"] == "PANEL_K"
    assert group["capture_id"] == "C1"  # the panel's selected exposure by default
    assert group["source_asset_id"] == "RAW1"
    assert group["frame_id"] == "F1"
    assert isinstance(group["width_px"], int) and isinstance(group["height_px"], int)
    assert group["width_px"] > 0 and group["height_px"] > 0
    assert group["signal_polarity"] in ("high", "low")
    assert group["source_confirmed"] is False
    assert group["saturation_value"] is None
    assert group["results"] is None
    assert [lane["lane_id"] for lane in group["lanes"]] == [1, 2, 3]
    for lane in group["lanes"]:
        assert "width_px" not in lane and "height_px" not in lane
        assert all(isinstance(lane[key], int) for key in ("x", "y", "background_x", "background_y"))
        assert 0 <= lane["x"] and lane["x"] + group["width_px"] <= WIDTH
        assert 0 <= lane["y"] and lane["y"] + group["height_px"] <= HEIGHT
        assert 0 <= lane["background_x"] and lane["background_x"] + group["width_px"] <= WIDTH
        assert 0 <= lane["background_y"] and lane["background_y"] + group["height_px"] <= HEIGHT

    second, other = start(client, project, capture_id="C2")
    assert other["capture_id"] == "C2"
    assert other["group_id"] != group["group_id"]
    assert len(second["quantification_groups"]) == 2


def test_default_rois_when_selection_has_no_crop(quant_project):
    root, client, project, _, _ = quant_project
    location = P.locate(root)
    stored = P.load_project(location)
    panel = next(p for p in stored.panels if p.panel_id == "PANEL_K")
    panel.display_geometry = DisplayGeometry(
        reference_frame_id="F1",
        lane_layout=LaneLayout(lane_count=3, centers_x=[1.0, 2.0, 3.0], status="candidate"),
    )
    stored = P.save_project(stored, location, stored.project_revision)
    project = client.get("/api/project").json()

    project, group = start(client, project)
    xs = sorted(lane["x"] for lane in group["lanes"])
    # Lane centers alone do not specify a band row without a crop.
    assert group['placement_method'] == 'fallback'
    assert xs[-1] - xs[0] > WIDTH / 2
    assert all(x > 10 for x in xs)


def test_start_rejects_non_raw_source(small_project):
    path, project = small_project
    client = TestClient(create_app(path))
    data = client.get("/api/project").json()
    bad = client.post(
        "/api/start_quantification",
        json={"panel_id": "PANEL_K", "capture_id": "C1", "expected_revision": data["project_revision"]},
    )
    assert bad.status_code == 422
    missing = client.post(
        "/api/start_quantification", json={"panel_id": "missing", "expected_revision": data["project_revision"]}
    )
    assert missing.status_code == 422


def test_start_rejects_eight_bit_raw(tmp_path):
    _, client, project, _, _ = build_project(tmp_path, np.full((HEIGHT, WIDTH), 100, dtype=np.uint8))
    bad = client.post(
        "/api/start_quantification",
        json={"panel_id": "PANEL_K", "capture_id": "C1", "expected_revision": project["project_revision"]},
    )
    assert bad.status_code == 422


def test_start_rejects_changed_source_bytes(quant_project):
    _, client, project, _, source_path = quant_project
    tifffile.imwrite(source_path, np.zeros((HEIGHT, WIDTH), dtype=np.uint16), photometric="miniswhite")
    bad = client.post(
        "/api/start_quantification",
        json={"panel_id": "PANEL_K", "capture_id": "C1", "expected_revision": project["project_revision"]},
    )
    assert bad.status_code == 422


def test_shared_size_only_and_fixed_centres(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = post(client, "update_quantification", project, group_id=group_id, lanes=LANES)
    group = group_of(project)
    original = [dict(lane) for lane in group["lanes"]]
    old_size = (group["width_px"], group["height_px"])

    project = post(
        client, "update_quantification", project, group_id=group_id, width_px=12, height_px=8
    )
    group = group_of(project)
    assert (group["width_px"], group["height_px"]) == (12, 8)
    for before, after in zip(original, group["lanes"]):
        assert "width_px" not in after and "height_px" not in after
        # The shared size changed for every lane; each centre stays put.
        assert abs((after["x"] + group["width_px"] / 2) - (before["x"] + old_size[0] / 2)) <= 1
        assert abs((after["y"] + group["height_px"] / 2) - (before["y"] + old_size[1] / 2)) <= 1
        assert abs((after["background_x"] + group["width_px"] / 2) - (before["background_x"] + old_size[0] / 2)) <= 1
        assert abs((after["background_y"] + group["height_px"] / 2) - (before["background_y"] + old_size[1] / 2)) <= 1

    # A per-lane size is not part of the contract.
    lanes = [dict(lane) for lane in group["lanes"]]
    lanes[0]["width_px"] = 5
    bad = client.post(
        "/api/update_quantification",
        json={
            "group_id": group_id,
            "lanes": lanes,
            "expected_revision": project["project_revision"],
        },
    )
    assert bad.status_code == 422


def test_translation_is_independent_per_axis_and_per_lane(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    before = [dict(lane) for lane in group["lanes"]]

    moved = [dict(lane) for lane in before]
    moved[1]["x"] += 2
    moved[1]["y"] -= 3
    project = post(client, "update_quantification", project, group_id=group_id, lanes=moved)
    lanes = group_of(project)["lanes"]
    assert (lanes[1]["x"], lanes[1]["y"]) == (before[1]["x"] + 2, before[1]["y"] - 3)
    assert (lanes[1]["background_x"], lanes[1]["background_y"]) == (before[1]["background_x"], before[1]["background_y"])
    assert lanes[0] == before[0] and lanes[2] == before[2]
    width, height = group_of(project)["width_px"], group_of(project)["height_px"]

    moved = [dict(lane) for lane in lanes]
    moved[0]["y"] += 4
    project = post(client, "update_quantification", project, group_id=group_id, lanes=moved)
    lanes = group_of(project)["lanes"]
    assert lanes[0]["x"] == before[0]["x"] and lanes[0]["y"] == before[0]["y"] + 4
    assert (group_of(project)["width_px"], group_of(project)["height_px"]) == (width, height)


def test_bounds_overlap_and_integer_validation(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    current = revision(client)

    too_wide = client.post(
        "/api/update_quantification",
        json={"group_id": group_id, "width_px": WIDTH + 1, "expected_revision": current},
    )
    assert too_wide.status_code == 422
    assert revision(client) == current

    lanes = [dict(lane) for lane in group["lanes"]]
    lanes[0]["x"] = WIDTH - 5
    outside = client.post(
        "/api/update_quantification",
        json={"group_id": group_id, "lanes": lanes, "expected_revision": current},
    )
    assert outside.status_code == 422
    assert revision(client) == current

    lanes = [dict(lane) for lane in group["lanes"]]
    lanes[0]["background_x"], lanes[0]["background_y"] = lanes[0]["x"], lanes[0]["y"]
    overlap = client.post(
        "/api/update_quantification",
        json={"group_id": group_id, "lanes": lanes, "expected_revision": current},
    )
    assert overlap.status_code == 422
    assert revision(client) == current

    lanes = [dict(lane) for lane in group["lanes"]]
    lanes[0]["x"] = 10.5
    fractional = client.post(
        "/api/update_quantification",
        json={"group_id": group_id, "lanes": lanes, "expected_revision": current},
    )
    assert fractional.status_code == 422


def test_compute_without_confirmation_uses_exact_float64_math(quant_project):
    _, client, project, pixels, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]

    project = configure(client, project, group_id, lanes=LANES, source_confirmed=False)
    project = compute(client, project, group_id)
    assert group_of(project)["source_confirmed"] is False
    rows = {row["lane_id"]: row for row in group_of(project)["results"]["rows"]}

    for lane in LANES:
        raw, mean = expected_sum(pixels, lane)
        assert rows[lane["lane_id"]]["area_px"] == ROI_W * ROI_H
        assert rows[lane["lane_id"]]["raw_sum"] == pytest.approx(raw, abs=1e-9)
        assert rows[lane["lane_id"]]["background_mean"] == pytest.approx(mean, abs=1e-9)
        if lane["lane_id"] == 2:
            assert rows[lane["lane_id"]]["net_signal"] is None
        else:
            assert rows[lane["lane_id"]]["net_signal"] == pytest.approx(raw - ROI_W * ROI_H * mean, abs=1e-9)

    # Below-background band stays negative instead of being clipped to zero.
    assert rows[3]["net_signal"] < 0
    # Lane 2 contains raw values at the dtype maximum: measurement is reported,
    # but the background-corrected value is withheld.
    assert rows[2]["saturated_pixels"] == len(SATURATED_PIXELS)
    assert rows[2]["status"] == "saturated"
    assert rows[2]["net_signal"] is None
    assert rows[1]["status"] == "ok"

    info = client.get(f"/api/quantification_info/{group_id}").json()
    assert info["width"] == WIDTH and info["height"] == HEIGHT
    assert info["results_current"] is True


def test_low_polarity_reverses_the_sign_and_negative_is_kept(quant_project):
    _, client, project, pixels, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)
    high = {row["lane_id"]: row["net_signal"] for row in group_of(project)["results"]["rows"]}

    project = post(client, "update_quantification", project, group_id=group_id, signal_polarity="low")
    assert group_of(project)["results"] is None
    project = compute(client, project, group_id)
    rows = {row["lane_id"]: row for row in group_of(project)["results"]["rows"]}

    for lane_id in (1, 3):
        raw, mean = expected_sum(pixels, next(lane for lane in LANES if lane["lane_id"] == lane_id))
        assert rows[lane_id]["raw_sum"] == pytest.approx(raw, abs=1e-9)
        assert rows[lane_id]["net_signal"] == pytest.approx(ROI_W * ROI_H * mean - raw, abs=1e-9)
        assert rows[lane_id]["net_signal"] == pytest.approx(-high[lane_id], abs=1e-9)


def test_excluded_lane_reports_measurement_without_net_signal(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id)
    lanes = [dict(lane) for lane in group["lanes"]]
    lanes[0]["excluded"] = True
    project = post(client, "update_quantification", project, group_id=group_id, lanes=lanes)
    project = compute(client, project, group_id)
    row = next(r for r in group_of(project)["results"]["rows"] if r["lane_id"] == 1)
    assert row["status"] == "excluded"
    assert row["net_signal"] is None
    assert row["raw_sum"] > 0


def test_compute_rejects_overlap_even_if_stored_out_of_band(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)
    # Write an overlapping layout directly, bypassing the update endpoint, so the
    # compute endpoint has to validate geometry itself.
    location = P.locate(quant_project[0])
    stored = P.load_project(location)
    stored_group = next(g for g in stored.quantification_groups if g.group_id == group_id)
    stored_group.lanes[0].background_x, stored_group.lanes[0].background_y = stored_group.lanes[0].x, stored_group.lanes[0].y
    stored = P.save_project(stored, location, stored.project_revision)
    bad = client.post(
        "/api/compute_quantification",
        json={"group_id": group_id, "expected_revision": stored.project_revision},
    )
    assert bad.status_code == 422


def test_every_update_clears_results(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)

    patches = [
        {"signal_polarity": "low"},
        {"saturation_value": 60000},
        {"lanes": [dict(lane, y=lane["y"] - 1) for lane in LANES]},
        {"source_confirmed": False},
    ]
    for patch in patches:
        project = compute(client, project, group_id)
        assert group_of(project)["results"] is not None
        project = post(client, "update_quantification", project, group_id=group_id, **patch)
        assert group_of(project)["results"] is None
        info = client.get(f"/api/quantification_info/{group_id}").json()
        assert info["results_current"] is False
        assert info.get("reason")


def test_display_mapping_is_display_only(quant_project):
    root, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)
    before = group_of(project)["results"]["rows"]

    location = P.locate(root)
    stored = P.load_project(location)
    source = next(s for s in stored.sources if s.source_asset_id == "RAW1")
    source.display_mapping = {
        "invert": True,
        "saturation_value": 65535,
        "low_percentile": 1.0,
        "high_percentile": 99.0,
    }
    stored = P.save_project(stored, location, stored.project_revision)

    info = client.get(f"/api/quantification_info/{group_id}").json()
    assert info["results_current"] is True, "display mapping must not invalidate measurements"
    assert client.get(f"/api/quantification_csv/{group_id}").status_code == 200

    project = client.get("/api/project").json()
    recomputed = compute(client, project, group_id)
    after = group_of(recomputed)["results"]["rows"]
    assert [(r["lane_id"], r["raw_sum"], r["net_signal"]) for r in after] == [
        (r["lane_id"], r["raw_sum"], r["net_signal"]) for r in before
    ]


def test_info_reports_stale_reasons(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    info = client.get(f"/api/quantification_info/{group_id}").json()
    assert info["results_current"] is False and info.get("reason")

    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)
    assert client.get(f"/api/quantification_info/{group_id}").json()["results_current"] is True

    project = post(client, "update_quantification", project, group_id=group_id, width_px=8, height_px=6)
    info = client.get(f"/api/quantification_info/{group_id}").json()
    assert info["results_current"] is False and info.get("reason")

    missing = client.get("/api/quantification_info/not-a-group")
    assert missing.status_code in (404, 422)


def test_stale_source_hash_blocks_compute_and_csv(quant_project):
    _, client, project, _, source_path = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)
    assert client.get(f"/api/quantification_info/{group_id}").json()["results_current"] is True

    pixels = np.full((HEIGHT, WIDTH), 200, dtype=np.uint16)
    tifffile.imwrite(source_path, pixels, photometric="miniswhite")

    info = client.get(f"/api/quantification_info/{group_id}").json()
    assert info["results_current"] is False
    assert client.get(f"/api/quantification_csv/{group_id}").status_code == 422
    bad = client.post(
        "/api/compute_quantification",
        json={"group_id": group_id, "expected_revision": project["project_revision"]},
    )
    assert bad.status_code == 422


def test_sample_and_lane_identity_invalidate_results(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)

    project = post(
        client, "set_lanes", project, panel_id="PANEL_K", lane_count=3, sample_names=["Sample X", "Sample Y", "Sample Z"]
    )
    assert client.get(f"/api/quantification_info/{group_id}").json()["results_current"] is False
    assert client.get(f"/api/quantification_csv/{group_id}").status_code == 422

    project = compute(client, project, group_id)
    rows = group_of(project)["results"]["rows"]
    assert [row["sample_name"] for row in rows] == ["Sample X", "Sample Y", "Sample Z"]

    project = post(
        client, "set_lanes", project, panel_id="PANEL_K", lane_count=2, sample_names=["Sample X", "Sample Y"]
    )
    assert client.get(f"/api/quantification_info/{group_id}").json()["results_current"] is False
    lanes_changed = client.post(
        "/api/compute_quantification",
        json={"group_id": group_id, "expected_revision": project["project_revision"]},
    )
    assert lanes_changed.status_code == 422


def test_csv_contains_conditions_coordinates_and_safe_names(quant_project):
    _, client, project, pixels, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)

    response = client.get(f"/api/quantification_csv/{group_id}")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header_index = next(i for i, row in enumerate(rows) if "lane_id" in row)
    header = rows[header_index]
    conditions = {row[0]: row[1] for row in rows[:header_index] if len(row) == 2}
    assert conditions["width_px"] == str(ROI_W)
    assert conditions["height_px"] == str(ROI_H)
    assert conditions["signal_polarity"] == "high"
    source_sha = next(row[1] for row in rows[:header_index] if row and row[0] == "source_sha256")
    assert source_sha == next(s for s in project["sources"] if s["source_asset_id"] == "RAW1")["sha256"]
    formula = next(row[1] for row in rows[:header_index] if row and row[0] == "formula")
    assert "raw_sum" in formula and "background_mean" in formula

    for key in ("sample_name", "area_px", "raw_sum", "background_mean", "net_signal", "saturated_pixels", "status",
                "x", "y", "background_x", "background_y"):
        assert key in header
    data = {int(row[header.index("lane_id")]): row for row in rows[header_index + 1 :]}
    assert len(data) == len(LANES)
    assert data[1][header.index("sample_name")] == "'=1+1"
    assert data[3][header.index("sample_name")] == "'-2+3"
    assert not data[1][header.index("sample_name")].startswith("=")
    lane1 = LANES[0]
    assert data[1][header.index("x")] == str(lane1["x"])
    assert data[1][header.index("y")] == str(lane1["y"])
    assert data[1][header.index("background_x")] == str(lane1["background_x"])
    assert data[1][header.index("background_y")] == str(lane1["background_y"])
    expected_raw, _ = expected_sum(pixels, lane1)
    net = float(data[1][header.index("net_signal")])
    assert net == pytest.approx(expected_raw - ROI_W * ROI_H * BACKGROUND_VALUE, abs=1e-9)
    assert data[2][header.index("net_signal")] == ""  # saturated: no value
    assert data[2][header.index("status")] == "saturated"
    assert data[3][header.index("status")] == "ok"


def test_csv_rejects_missing_or_stale_results(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]
    assert client.get(f"/api/quantification_csv/{group_id}").status_code == 422
    assert client.get("/api/quantification_csv/not-a-group").status_code in (404, 422)

    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)
    assert client.get(f"/api/quantification_csv/{group_id}").status_code == 200
    project = post(client, "update_quantification", project, group_id=group_id, signal_polarity="low")
    assert client.get(f"/api/quantification_csv/{group_id}").status_code == 422


def test_source_preview_is_full_resolution_and_display_only(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    group_id = group["group_id"]

    first = client.get(f"/api/quantification_source/{group_id}.png")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/png")
    with Image.open(io.BytesIO(first.content)) as image:
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "L"

    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)
    rows_before = group_of(project)["results"]["rows"]
    second = client.get(f"/api/quantification_source/{group_id}.png")
    assert second.status_code == 200
    with Image.open(io.BytesIO(second.content)) as image:
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "L"
    assert second.content == first.content
    assert group_of(client.get("/api/project").json())["results"]["rows"] == rows_before
    assert client.get(f"/api/quantification_info/{group_id}").json()["results_current"] is True


def test_revision_conflicts_are_reported_for_new_operations(quant_project):
    _, client, project, _, _ = quant_project
    stale = project["project_revision"] - 1
    for name, payload in (
        ("start_quantification", {"panel_id": "PANEL_K"}),
        ("update_quantification", {"group_id": "missing", "source_confirmed": True}),
        ("compute_quantification", {"group_id": "missing"}),
    ):
        response = client.post("/api/" + name, json={**payload, "expected_revision": stale})
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["current_revision"] == project["project_revision"]


def test_quantification_does_not_block_figure_output(quant_project):
    root, client, project, _, _ = quant_project
    before = api.validate(root)["output_readiness"]
    project, group = start(client, project)
    group_id = group["group_id"]
    project = configure(client, project, group_id, lanes=LANES)
    project = compute(client, project, group_id)
    after = api.validate(root)["output_readiness"]
    assert after == before
    exported = api.export_project(root, "png")
    assert Path(exported["path"]).is_file()


def test_third_stage_tab_is_exactly_optional():
    app = Path(__file__).resolve().parents[1] / "src/wb/web/app/src/App.svelte"
    text = app.read_text(encoding="utf-8")
    assert text.count("3. 定量(Optional)") == 1


def test_excel_preserves_numbers_names_and_conditions(quant_project):
    from openpyxl import load_workbook
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    project = configure(client, project, group['group_id'], lanes=LANES)
    project = compute(client, project, group['group_id'])
    response = client.get(f"/api/quantification_excel/{group['group_id']}")
    assert response.status_code == 200
    assert 'spreadsheetml.sheet' in response.headers['content-type']
    assert 'quantification.xlsx' in response.headers['content-disposition']
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ['定量結果', '解析条件']
    sheet = book['定量結果']
    assert sheet['B2'].value == '=1+1' and sheet['B2'].data_type == 's'
    assert sheet['B4'].value == '-2+3' and sheet['B4'].data_type == 's'
    assert sheet['C2'].value == ROI_W * ROI_H
    assert sheet['F2'].value == group_of(project)['results']['rows'][0]['net_signal']
    assert sheet['F2'].data_type == 'n'
    assert sheet['F3'].value is None  # saturation remains missing
    assert sheet['F4'].value < 0
    assert sheet.freeze_panes == 'A2'
    conditions = dict(book['解析条件'].iter_rows(min_row=2, values_only=True))
    assert conditions['source_sha256'] == project['sources'][0]['sha256']
    assert conditions['exposure_s'] == 1
    assert conditions['formula'] == 'raw_sum - area_px * background_mean'
    project = post(client, 'update_quantification', project, group_id=group['group_id'], source_confirmed=False)
    assert client.get(f"/api/quantification_excel/{group['group_id']}").status_code == 422


def test_resize_keeps_all_adjusted_centres_without_drift(quant_project):
    _, client, project, _, _ = quant_project
    project, group = start(client, project)
    moved = [dict(lane) for lane in LANES]
    moved[1].update(x=39, y=28, background_x=38, background_y=9)
    project = configure(client, project, group['group_id'], lanes=moved)
    def centres(g):
        return [(2*l['x']+g['width_px'], 2*l['y']+g['height_px'],
                 2*l['background_x']+g['width_px'], 2*l['background_y']+g['height_px']) for l in g['lanes']]
    before = centres(group_of(project))
    for w,h in [(12,8),(8,4),(14,10),(10,6)]:
        project = post(client, 'update_quantification', project, group_id=group['group_id'], width_px=w,height_px=h)
        assert centres(group_of(project)) == before
    assert group_of(project)['lanes'] == moved
    for patch in [dict(width_px=11), dict(width_px=12,lanes=moved)]:
        response=client.post('/api/update_quantification',json=dict(group_id=group['group_id'],expected_revision=project['project_revision'],**patch))
        assert response.status_code == 422


@pytest.mark.parametrize('missing_kind', ['link', 'display_tiff', 'unidentified_source', 'file'])
def test_raw16_required_for_start_compute_and_exports(quant_project, missing_kind):
    root, client, project, _, source_path = quant_project
    project, group = start(client, project)
    project = configure(client, project, group['group_id'], lanes=LANES)
    project = compute(client, project, group['group_id'])
    stored = P.load_project(P.locate(root))
    if missing_kind == 'link':
        stored.captures[0].analysis_source_asset_id = None
    elif missing_kind == 'display_tiff':
        # Even uint16 grayscale must not qualify when it is a display export.
        stored.sources[0].representation_kind = 'presentation_export_candidate'
    elif missing_kind == 'unidentified_source':
        stored.sources[0].representation_kind = 'analysis_candidate'
    else:
        source_path.unlink()
    stored = P.save_project(stored, P.locate(root), stored.project_revision)
    for operation, payload in [
        ('start_quantification', {'panel_id': 'PANEL_K', 'capture_id': 'C1'}),
        ('compute_quantification', {'group_id': group['group_id']}),
    ]:
        response = client.post('/api/' + operation, json={**payload, 'expected_revision': stored.project_revision})
        assert response.status_code == 422
        assert 'raw16元画像がないため定量できません' in response.text
    info = client.get('/api/quantification_info/' + group['group_id']).json()
    assert not info['results_current']
    assert 'raw16元画像がないため定量できません' in info['reason']
    for output in ('csv', 'excel'):
        assert client.get('/api/quantification_' + output + '/' + group['group_id']).status_code == 422
    assert len(client.get('/api/project').json()['quantification_groups']) == 1
