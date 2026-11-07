import importlib.util
from pathlib import Path

import pytest

from wb import api
from wb.layout import compute_layout
from wb.model import MarkerXRange
from wb.rendering import marker_export_rects, render_marker

_spec = importlib.util.spec_from_file_location("export_fixture", Path(__file__).parent / "fixtures/export/generate.py")
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)

# PANEL_A: crop x 20-70, y 10-20; registered marker lane x 5-15; marker frame 80 x 60.


@pytest.fixture
def project(tmp_path):
    return _fixture.make_project(tmp_path)


def _images(layout):
    return [e for page in layout["pages"] for e in page["elements"] if e["kind"] == "image"]


def test_trimmed_marker_keeps_the_band_height_and_scale(project, tmp_path):
    project.panels[0].display_geometry.marker_x = MarkerXRange(x0=7, x1=13)
    blot, marker = _images(compute_layout(project))
    image = render_marker(project, tmp_path, "PANEL_A")["image"]
    assert (marker["y"], marker["h"]) == (blot["y"], blot["h"])
    assert image.height == blot["pixel_height"]
    assert marker["w"] / image.width == pytest.approx(blot["w"] / blot["pixel_width"])
    rects = marker_export_rects(project, project.panels[0])
    assert (rects["main"].x0, rects["main"].y0, rects["main"].x1, rects["main"].y1) == (7, 10, 13, 20)
    assert list(rects) == ["main"]


def test_set_marker_x_validates_saves_and_resets(project, tmp_path):
    revision = project.project_revision
    with pytest.raises(ValueError, match="はみ出し"):
        api.set_marker_x(tmp_path, "PANEL_A", -30, 10, revision)
    with pytest.raises(ValueError, match="左右"):
        api.set_marker_x(tmp_path, "PANEL_A", 7, None, revision)
    saved = api.set_marker_x(tmp_path, "PANEL_A", 7, 13, revision)
    assert saved.panels[0].display_geometry.marker_x.model_dump() == {"x0": 7, "x1": 13}
    entry = next(p for p in api.panel_summaries(saved) if p["panel_id"] == "PANEL_A")
    assert entry["marker_source"] == "manual"
    assert entry["marker_rect"] == {"x0": 7, "y0": 10, "x1": 13, "y1": 20}
    cleared = api.set_marker_x(tmp_path, "PANEL_A", None, None, saved.project_revision)
    assert cleared.panels[0].display_geometry.marker_x is None


def test_vertical_crop_edits_move_the_marker_with_the_bands(project, tmp_path):
    saved = api.set_marker_x(tmp_path, "PANEL_A", 7, 13, project.project_revision)
    moved = api.adjust(tmp_path, "PANEL_A", saved.project_revision, crop_top=5, crop_bottom=22)
    panel = moved.panels[0]
    assert panel.display_geometry.marker_x.model_dump() == {"x0": 7, "x1": 13}
    blot, marker = _images(compute_layout(moved))
    assert (marker["y"], marker["h"]) == (blot["y"], blot["h"])
    main = marker_export_rects(moved, panel)["main"]
    assert (main.y0, main.y1) == (5, 22)
