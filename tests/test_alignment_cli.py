"""Integration tests for numerical band alignment and inspection across API, CLI, app, and Skill."""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient

from wb import api
from wb.app import create_app
from wb.cli import main
from wb.geometry import Affine, polygon_within_frame


def _make_tilted_blot_image(
    width: int = 440,
    height: int = 320,
    tilt_deg: float = 4.5,
    lane_count: int = 5,
    include_saturation: bool = False,
) -> np.ndarray:
    """Create a synthetic RGB blot image with dark bands tilted by `tilt_deg`."""
    img = np.full((height, width, 3), 245, dtype=np.uint8)
    cx, cy = width * 0.5, height * 0.5
    rad = math.radians(tilt_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    lane_xs = np.linspace(90.0, width - 90.0, lane_count)
    row_ys = [cy - 35.0, cy + 35.0]

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    signal = np.zeros((height, width), dtype=np.float64)

    for row_y in row_ys:
        for lx in lane_xs:
            # Rotate band center (lx, row_y) about (cx, cy)
            dx0, dy0 = lx - cx, row_y - cy
            bx = cx + cos_a * dx0 - sin_a * dy0
            by = cy + sin_a * dx0 + cos_a * dy0

            # Local coordinates aligned with the tilted band
            dx = xx - bx
            dy = yy - by
            u = cos_a * dx + sin_a * dy
            v = -sin_a * dx + cos_a * dy
            band = np.exp(-0.5 * ((u / 14.0) ** 2 + (v / 3.5) ** 2))
            signal = np.maximum(signal, band)

    gray = np.clip(245.0 - 190.0 * signal, 20, 245).astype(np.uint8)
    img[..., 0] = gray
    img[..., 1] = gray
    img[..., 2] = gray

    if include_saturation:
        # Place a small ChemiDoc red saturation marker inside one band
        img[int(cy - 37) : int(cy - 33), int(lane_xs[0] - 2) : int(lane_xs[0] + 2)] = (255, 0, 0)
    return img


@pytest.fixture
def tilted_project(tmp_path: Path) -> Path:
    folder = tmp_path / "synthetic_blot"
    folder.mkdir()
    img = _make_tilted_blot_image(tilt_deg=4.5, include_saturation=True)
    tif_path = folder / "TargetA_2000-01-01 10.000s(Chemiluminescence).tif"
    tifffile.imwrite(tif_path, img, photometric="rgb")
    info = api.init_project(folder)
    return Path(info["path"])


def test_api_inspect_and_auto_align_panel(tilted_project: Path):
    proj = api._load(tilted_project)
    saved = api.add_manual_membrane(
        tilted_project,
        proj.project_revision,
        rect={"x0": 30, "y0": 40, "x1": 410, "y1": 280},
        label="A",
        auto_rotation=False,
    )
    panel = saved.panels[0]

    inspected = api.inspect_panel_bands(tilted_project, panel.panel_id)
    assert inspected["panel_id"] == panel.panel_id
    assert inspected["label"] == "A"
    assert inspected["current_rotation_deg"] == pytest.approx(0.0, abs=1e-6)
    assert inspected["saturation_fraction"] > 0.0
    assert len(inspected["row_positions_y"]) >= 2
    assert len(inspected["band_rows"]) >= 2
    assert len(inspected["lane_peaks_x"]) >= 4
    assert inspected["residual_tilt_deg"] == pytest.approx(-4.5, abs=0.8)
    assert inspected["recommended_rotation_deg"] == pytest.approx(
        inspected["current_rotation_deg"] + inspected["residual_tilt_deg"], abs=1e-6
    )

    # Dry run does not mutate project revision
    dry = api.auto_align_panel(tilted_project, panel.panel_id, saved.project_revision, dry_run=True)
    assert dry["alignment"]["applied"] is False
    assert dry["project"].project_revision == saved.project_revision
    assert abs(dry["alignment"]["delta_deg"] + 4.5) < 0.8

    # Applied auto-align updates revision and levels the bands
    applied = api.auto_align_panel(tilted_project, panel.panel_id, saved.project_revision, dry_run=False)
    assert applied["alignment"]["applied"] is True
    aligned_proj = applied["project"]
    assert aligned_proj.project_revision == saved.project_revision + 1
    for key in (
        "previous_rotation_deg",
        "estimated_rotation_deg",
        "delta_deg",
        "confidence",
        "method",
        "applied",
        "band_rows",
        "metrics",
        "warnings",
    ):
        assert key in applied["alignment"]

    after = api.inspect_panel_bands(tilted_project, panel.panel_id)
    assert abs(after["residual_tilt_deg"]) < 0.6

    # Undo restores previous rotation
    undone = api.undo(tilted_project, aligned_proj.project_revision)
    after_undo = api.inspect_panel_bands(tilted_project, panel.panel_id)
    assert after_undo["current_rotation_deg"] == pytest.approx(0.0, abs=1e-6)
    assert undone.project_revision == aligned_proj.project_revision + 1


def test_add_manual_membrane_auto_rotation(tilted_project: Path):
    proj = api._load(tilted_project)
    saved = api.add_manual_membrane(
        tilted_project,
        proj.project_revision,
        rect={"x0": 20, "y0": 20, "x1": 420, "y1": 300},
        label="B",
        auto_rotation=True,
    )
    panel = saved.panels[0]
    inspected = api.inspect_panel_bands(tilted_project, panel.panel_id)
    assert abs(inspected["current_rotation_deg"] + 4.5) < 0.8
    assert abs(inspected["residual_tilt_deg"]) < 0.6

    # Ensure crop_rect corners remain inside reference frame
    dg = panel.display_geometry
    ref = next(f for f in saved.frames if f.frame_id == dg.reference_frame_id)
    tr = Affine.from_json(
        next(t for t in saved.transforms if t.transform_id == dg.reference_to_rectified_transform_id).model_dump()
    )
    from wb.geometry import Rect

    crop = Rect(**dg.crop_rect.model_dump())
    assert polygon_within_frame(tr.inverse().apply(crop.corners()), ref.width, ref.height)


def test_cli_inspect_adjust_and_add_membrane_auto_rotation(tilted_project: Path, capsys):
    assert main(["add-membrane", "30", "40", "410", "280", "--auto-rotation", "--label", "A", "--project", str(tilted_project), "--json"]) == 0
    added = json.loads(capsys.readouterr().out)
    assert added["ok"] is True

    assert main(["inspect", "A", "--project", str(tilted_project), "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["label"] == "A"
    assert abs(inspected["residual_tilt_deg"]) < 0.6

    # Conflicting flags --auto-rotation and --rotation should fail cleanly
    assert main(["adjust", "A", "--auto-rotation", "--rotation", "2.0", "--project", str(tilted_project), "--json"]) == 1
    err = json.loads(capsys.readouterr().out)
    assert "error" in err

    # Manual tilt followed by CLI --auto-rotation
    assert main(["adjust", "A", "--rotation", "3.0", "--project", str(tilted_project), "--json"]) == 0
    capsys.readouterr()

    assert main(["adjust", "A", "--auto-rotation", "--dry-run", "--project", str(tilted_project), "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["ok"] is True
    assert dry["alignment"]["applied"] is False

    assert main(["adjust", "A", "--auto-rotation", "--project", str(tilted_project), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] is True
    assert res["alignment"]["applied"] is True
    assert abs(res["alignment"]["estimated_rotation_deg"] + 4.5) < 0.8


def test_app_inspect_and_auto_align_endpoints(tilted_project: Path):
    proj = api._load(tilted_project)
    saved = api.add_manual_membrane(
        tilted_project,
        proj.project_revision,
        rect={"x0": 30, "y0": 40, "x1": 410, "y1": 280},
        label="A",
        auto_rotation=False,
    )
    panel_id = saved.panels[0].panel_id

    client = TestClient(create_app(tilted_project))
    resp = client.get(f"/api/inspect/{panel_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["panel_id"] == panel_id
    assert abs(data["residual_tilt_deg"] + 4.5) < 0.8

    post_resp = client.post(
        "/api/auto_align_panel",
        json={"panel_id": panel_id, "expected_revision": saved.project_revision},
    )
    assert post_resp.status_code == 200
    body = post_resp.json()
    assert body["alignment"]["applied"] is True
    assert body["project"]["project_revision"] == saved.project_revision + 1


def test_skill_files_are_identical():
    repo_root = Path(__file__).resolve().parent.parent
    root_skill = (repo_root / "skills" / "wblot" / "SKILL.md").read_text(encoding="utf-8")
    pkg_skill = (repo_root / "src" / "wb" / "skills" / "wblot" / "SKILL.md").read_text(encoding="utf-8")
    assert root_skill == pkg_skill
