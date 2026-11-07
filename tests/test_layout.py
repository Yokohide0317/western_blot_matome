import importlib.util
import json
from pathlib import Path

import pytest

from wb.layout import compute_layout
from wb import project as projects
from wb.rendering import render_marker, render_panel

_spec = importlib.util.spec_from_file_location("export_fixture", Path(__file__).parent / "fixtures/export/generate.py")
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


@pytest.fixture
def project(tmp_path):
    return _fixture.make_project(tmp_path)


def elements(layout, kind):
    return [e for page in layout["pages"] for e in page["elements"] if e["kind"] == kind]


def test_lane_width_aspect_marker_scale_and_empty_columns(project, tmp_path):
    layout = compute_layout(project)
    assert json.loads(json.dumps(layout)) == layout
    assert layout["slide"] == {"w": 13.333, "h": 7.5}
    table, = elements(layout, "table")
    blot, marker = elements(layout, "image")
    assert table["names"] == ["control", "", ""]
    assert blot["x"] == table["sample_x"]
    assert blot["w"] == table["sample_w"]
    assert table["column_w"] * table["columns"] == pytest.approx(blot["w"])
    rendered = render_panel(project, tmp_path, "PANEL_A", "C2")
    rendered_marker = render_marker(project, tmp_path, "PANEL_A")["image"]
    assert blot["h"] / blot["w"] == pytest.approx(rendered.height / rendered.width)
    assert marker["y"] == blot["y"]
    assert marker["h"] == blot["h"]
    assert marker["w"] / rendered_marker.width == pytest.approx(blot["w"] / rendered.width)
    assert marker["h"] / rendered_marker.height == pytest.approx(blot["h"] / rendered.height)
    geometry = layout["geometry"]
    assert geometry["mw_x"] >= marker["x"] + marker["w"]
    assert geometry["mw_x"] + geometry["mw_w"] <= layout["slide"]["w"] - 0.4 + 1e-9


def test_adoption_changes_only_the_blot_reference(project):
    before = compute_layout(project)
    changed = projects.set_selected_exposure(project, "PANEL_A", "C1")
    after = compute_layout(changed)
    images = elements(after, "image")
    assert images[0]["capture_id"] == "C1"
    images[0]["capture_id"] = "C2"
    assert after == before


def test_null_lane_count_never_infers_a_table_from_names(project):
    project.sample_groups[0].lane_count = None
    layout = compute_layout(project)
    assert not elements(layout, "table")
    assert len(layout["warnings"]) == 1


def test_sample_table_columns_follow_custom_lane_boundaries(project):
    layout = compute_layout(project)
    lane_w = layout["geometry"]["lane_w"]
    table, = elements(layout, "table")
    assert table["column_widths"] == pytest.approx([lane_w / 3] * 3)
    project.panels[0].display_geometry.lane_layout.boundaries = [0, 0.2, 0.5, 1]
    table, = elements(compute_layout(project), "table")
    assert table["column_widths"] == pytest.approx([0.2 * lane_w, 0.3 * lane_w, 0.5 * lane_w])
    assert sum(table["column_widths"]) == pytest.approx(lane_w)
    assert table["column_w"] * 3 == pytest.approx(lane_w)  # legacy field preserved


def test_differing_lane_boundaries_repeat_the_sample_table(tmp_path):
    project = _fixture.make_project(tmp_path, panel_count=2)
    assert len(elements(compute_layout(project), "table")) == 1
    project.panels[0].display_geometry.lane_layout.boundaries = [0, 0.2, 0.5, 1]
    assert len(elements(compute_layout(project), "table")) == 2
    project.panels[1].display_geometry.lane_layout.boundaries = [0, 0.2, 0.5, 1]
    assert len(elements(compute_layout(project), "table")) == 1


def test_page_break_repeats_table_without_stretching(tmp_path):
    project = _fixture.make_project(tmp_path, panel_count=8)
    layout = compute_layout(project)
    assert len(layout["pages"]) > 1
    for page in layout["pages"]:
        assert sum(e["kind"] == "table" for e in page["elements"]) == 1
        assert sum(e.get("role") == "draft" for e in page["elements"]) == 0
        for e in page["elements"]:
            assert e["y"] + e["h"] <= 7.2 + 1e-9
            if e["kind"] == "image" and not e.get("marker"):
                assert e["h"] / e["w"] == pytest.approx(20 / 100)


def test_display_order_and_unset_geometry_placeholders(tmp_path):
    project = _fixture.make_project(tmp_path, panel_count=2)
    project.panels[0].display_geometry = None
    project.panels[1].display_order = 0
    project.panels[1].label = "Actin"
    layout = compute_layout(project)
    labels = [e for e in elements(layout, "text") if e.get("role") == "label"]
    assert [e["text"] for e in labels] == ["Actin", "A"]
    placeholder, = elements(layout, "placeholder")
    assert placeholder["panel_id"] == "PANEL_A"
    assert placeholder["text"] == "未設定"
    assert not any(e["panel_id"] == "PANEL_A" for e in elements(layout, "image"))


def test_placeholder_rows_paginate_and_repeat_table(tmp_path):
    project = _fixture.make_project(tmp_path, panel_count=12)
    for panel in project.panels:
        panel.display_geometry = None
    layout = compute_layout(project)
    assert len(layout["pages"]) > 1
    for page in layout["pages"]:
        assert sum(e["kind"] == "table" for e in page["elements"]) == 1
        assert all(e["y"] + e["h"] <= 7.2 + 1e-9 for e in page["elements"])


def test_tall_panel_shrinks_uniformly_to_fit(project):
    project.panels[0].display_geometry.crop_rect.y1 = 200
    layout = compute_layout(project)
    blot, marker = elements(layout, "image")
    assert blot["h"] / blot["w"] == pytest.approx(380 / 100)
    assert blot["y"] + blot["h"] <= 7.2 + 1e-9
    assert marker["h"] == blot["h"]


def test_fractional_crop_height_uses_renderer_padding(project, tmp_path):
    project.panels[0].display_geometry.crop_rect.y1 = 20.2
    blot, marker = elements(compute_layout(project), "image")
    rendered = render_panel(project, tmp_path, "PANEL_A", "C2")
    assert blot["pixel_height"] == rendered.height == 21
    assert blot["content_height"] == pytest.approx(20.4)
    assert marker["h"] == blot["h"]


def test_excluded_panel_has_no_layout_elements(tmp_path):
    project = _fixture.make_project(tmp_path, panel_count=2)
    project.panels[0].included = False
    layout = compute_layout(project)
    assert not any(e.get("panel_id") == "PANEL_A" for page in layout["pages"] for e in page["elements"])
    assert any(e.get("panel_id") == "PANEL_B" for page in layout["pages"] for e in page["elements"])


def test_pptx_marker_strips_align_to_blot_and_stay_on_slide(project, tmp_path):
    layout = compute_layout(project, pptx_markers=True)
    assert json.loads(json.dumps(layout)) == layout
    images = [e for e in elements(layout, "image")]
    blot = next(e for e in images if not e.get("marker"))
    strips = [e for e in images if e.get("marker")]
    assert [s["part"] for s in strips] == ["main"]
    for strip in strips:
        image = render_marker(project, tmp_path, "PANEL_A", part=strip["part"], rect=strip["rect"])["image"]
        assert strip["pixel_width"] == image.width
        assert strip["pixel_height"] == image.height
        assert strip["w"] / image.width == pytest.approx(blot["w"] / blot["pixel_width"])
        assert strip["h"] / image.height == pytest.approx(blot["h"] / blot["pixel_height"])
        assert strip["x"] >= 0 and strip["y"] >= 0
        assert strip["x"] + strip["w"] <= 13.333 + 1e-9
        assert strip["y"] + strip["h"] <= 7.5 + 1e-9
        assert strip["y"] == blot["y"] and strip["h"] == blot["h"]
    assert strips[0]["x"] == layout["geometry"]["marker_x"]
    for a, b in zip(strips, strips[1:]):
        assert b["x"] == pytest.approx(a["x"] + a["w"])
    assert strips[-1]["x"] + strips[-1]["w"] <= layout["geometry"]["mw_x"] + 1e-9
    # Every strip keeps the band crop's exact vertical range, so it cannot drift from the bands.
    crop = project.panels[0].display_geometry.crop_rect
    assert all(s["rect"][1] == crop.y0 and s["rect"][3] == crop.y1 for s in strips)
    # The default layout is untouched: one marker image, no part/rect.
    default = compute_layout(project)
    assert len([e for e in elements(default, "image") if e.get("marker")]) == 1
    assert not any("part" in e or "rect" in e for page in default["pages"] for e in page["elements"])


def test_pptx_marker_strips_omit_sides_leaving_the_frame_and_unregistered(project, tmp_path):
    project.panels[0].display_geometry.crop_rect.x0 = 65
    project.panels[0].display_geometry.crop_rect.x1 = 80
    from wb.rendering import marker_export_rects
    rects = marker_export_rects(project, project.panels[0])
    # Only the marker itself is exported; no context strips beside the crop.
    assert list(rects) == ["main"]
    parts = [e["part"] for e in elements(compute_layout(project, pptx_markers=True), "image") if e.get("marker")]
    assert parts == ["main"]
    with pytest.raises(ValueError, match="unavailable"):
        render_marker(project, tmp_path, "PANEL_A", part="left")
    # Missing registration keeps the marker_note path, never invented strips.
    project.panels[0].marker_registration_id = None
    layout = compute_layout(project, pptx_markers=True)
    assert not any(e.get("marker") for e in elements(layout, "image"))
    assert not any(e.get("role") == "marker_note" for e in elements(layout, "text"))


def test_pptx_marker_strips_never_fabricate_out_of_frame_pixels(project, tmp_path):
    # Registered ROI is valid, but the crop maps outside the marker source frame:
    # PPTX must omit strips entirely rather than render white source pixels.
    project.panels[0].display_geometry.crop_rect.y0 = 70
    project.panels[0].display_geometry.crop_rect.y1 = 80
    from wb.rendering import marker_export_rects
    assert marker_export_rects(project, project.panels[0]) == {}
    layout = compute_layout(project, pptx_markers=True)
    assert not any(e.get("marker") for e in elements(layout, "image"))
    assert not any(e.get("role") == "marker_note" for e in elements(layout, "text"))
    with pytest.raises(ValueError, match="unavailable"):
        render_marker(project, tmp_path, "PANEL_A", part="main")
    # The preview renderer is independent and unchanged.
    assert render_marker(project, tmp_path, "PANEL_A")["image"] is not None


def test_sample_spans_leave_physical_gap_without_extra_lane(project):
    project.panels[0].display_geometry.lane_layout.spans = [(0, .2), (.2, .4), (.6, 1)]
    table, = elements(compute_layout(project), 'table')
    assert table['sample_lane_count'] == 3
    assert table['columns'] == 4
    assert table['gap_columns'] == [2]
    assert table['names'] == ['control', '', '', '']
    assert table['column_widths'] == pytest.approx([table['sample_w'] * w for w in (.2, .2, .2, .4)])
    assert json.loads(json.dumps(compute_layout(project))) == compute_layout(project)


@pytest.mark.parametrize('with_gap', [False, True])
def test_opt_in_lane_scale_preserves_three_lane_membrane_width(tmp_path, with_gap):
    from wb.model import SampleGroup
    project = _fixture.make_project(tmp_path, panel_count=2)
    a, b = project.panels
    a.display_geometry.lane_layout.lane_count = 9
    b.display_geometry.lane_layout.lane_count = 3
    project.sample_groups[0].lane_count = 9
    project.sample_groups.append(SampleGroup(sample_group_id='THREE', lane_count=3, samples=[{}, {}, {}]))
    b.sample_group_id = 'THREE'
    if with_gap:
        a.display_geometry.lane_layout.spans = [(i / 10, (i + 1) / 10) for i in (0,1,2,4,5,6,7,8,9)]
    assert project.figure_scale_mode == 'equal_lane_width'
    layout = compute_layout(project)
    blots = [e for e in elements(layout, 'image') if not e.get('marker')]
    ratio = .3 if with_gap else 1 / 3
    assert blots[1]['w'] / blots[0]['w'] == pytest.approx(ratio)
    assert blots[1]['h'] / blots[0]['h'] == pytest.approx(ratio)  # no aspect distortion
    tables = elements(layout, 'table')
    assert tables[1]['sample_w'] == blots[1]['w']
    a_lane = next(w for i,w in enumerate(tables[0]['column_widths']) if i not in tables[0]['gap_columns'])
    assert tables[1]['column_widths'][0] == pytest.approx(a_lane)
    for blot in blots:
        marker = next(e for e in elements(layout, 'image') if e.get('marker') and e['panel_id'] == blot['panel_id'])
        assert marker['x'] == pytest.approx(blot['x'] + blot['w'])
        assert marker['h'] == blot['h']


def test_lane_scale_does_not_infer_unknown_count_from_candidate_peaks(project):
    project.figure_scale_mode = 'equal_lane_width'
    project.panels[0].display_geometry.lane_layout.lane_count = None
    project.panels[0].display_geometry.lane_layout.centers_x = [1, 3, 5]
    project.sample_groups[0].lane_count = None
    layout = compute_layout(project)
    assert layout['panel_width_factors'] == {'PANEL_A': 1}
    assert any('統一対象に含めていません' in warning['text'] for warning in layout['warnings'])
