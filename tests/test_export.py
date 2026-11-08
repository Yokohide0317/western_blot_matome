import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import numpy as np
from PIL import Image
import pytest

from wb import export, project as projects
from wb.layout import compute_layout
from wb.rendering import render_marker, render_panel

_spec = importlib.util.spec_from_file_location("export_fixture", Path(__file__).parent / "fixtures/export/generate.py")
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


@pytest.fixture
def project(tmp_path):
    return _fixture.make_project(tmp_path)


def test_pptx_table_editable_label_adopted_picture_and_notes(project, tmp_path):
    adopted = render_panel(project, tmp_path, "PANEL_A", "C2")
    other = render_panel(project, tmp_path, "PANEL_A", "C1")
    project.panels[0].label = "TargetA"
    path = tmp_path / "exports" / "figure.pptx"
    assert export.export_pptx(project, tmp_path, path, merge=False) == path
    with ZipFile(path) as package:
        slide = ET.fromstring(package.read("ppt/slides/slide1.xml"))
        assert slide.find(".//a:tbl", NS) is not None
        text = [e.text for e in slide.findall(".//a:t", NS)]
        assert "TargetA" in text
        assert "下書き" not in text
        pictures = [package.read(n) for n in package.namelist() if n.startswith("ppt/media/")]
        assert adopted.png in pictures
        assert other.png not in pictures
        # blot + the marker strip only
        for part in ("main",):
            element = next(
                e for e in compute_layout(project, pptx_markers=True)["pages"][0]["elements"]
                if e.get("part") == part
            )
            strip = render_marker(project, tmp_path, "PANEL_A", part=part, rect=element["rect"])["image"]
            assert strip.png in pictures
        assert len(pictures) == 2
        notes = package.read("ppt/notesSlides/notesSlide1.xml").decode()
        assert "PANEL_A: exposure=C2 geometry_revision=4" in notes
        assert "mode:" not in notes and "final:" not in notes


def test_pptx_sample_table_follows_custom_lane_boundaries(project, tmp_path):
    project.panels[0].display_geometry.lane_layout.boundaries = [0, 0.2, 0.5, 1]
    lane_w = compute_layout(project, pptx_markers=True)["geometry"]["lane_w"]
    path = export.export_pptx(project, tmp_path, tmp_path / "widths.pptx", merge=False)
    with ZipFile(path) as package:
        slide = ET.fromstring(package.read("ppt/slides/slide1.xml"))
        cols = [int(c.get("w")) for c in slide.findall(".//a:gridCol", NS)]
    assert cols[1:] == [int(w * 914400) for w in (0.2 * lane_w, 0.3 * lane_w, 0.5 * lane_w)]


def test_no_table_when_lanes_are_unset_and_placeholder_is_editable(project, tmp_path):
    project.sample_groups[0].lane_count = None
    project.panels[0].display_geometry = None
    path = export.export_pptx(project, tmp_path, tmp_path / "draft.pptx", merge=False)
    with ZipFile(path) as package:
        slide = ET.fromstring(package.read("ppt/slides/slide1.xml"))
        assert slide.find(".//a:tbl", NS) is None
        assert "未設定" in [e.text for e in slide.findall(".//a:t", NS)]


def test_png_size_nonwhite_blot_and_adoption_metadata(project, tmp_path):
    path = tmp_path / "figure.png"
    assert export.export_png(project, tmp_path, path) == path
    layout = compute_layout(project)
    blot = next(e for e in layout["pages"][0]["elements"] if e["kind"] == "image")
    with Image.open(path) as image:
        assert image.size == (4000, 2250)
        metadata = json.loads(image.info["wb_output"])
        assert "final" not in metadata and "mode" not in metadata
        assert metadata["panels"][0] == {"panel_id": "PANEL_A", "capture_id": "C2", "geometry_revision": 4}
        pixels = np.array(image)
    x, y, w, h = [round(blot[k] * 300) for k in ("x", "y", "w", "h")]
    assert np.any(pixels[y:y + h, x:x + w] < 100)
    assert not any(e.get("role") == "draft" for e in layout["pages"][0]["elements"])


def test_png_changes_when_adopted_capture_changes(project, tmp_path):
    a = export.export_png(project, tmp_path, tmp_path / "a.png", dpi=72)
    changed = projects.set_selected_exposure(project, "PANEL_A", "C1")
    b = export.export_png(changed, tmp_path, tmp_path / "b.png", dpi=72)
    with Image.open(a) as first, Image.open(b) as second:
        assert np.any(np.array(first) != np.array(second))


def test_pptx_and_png_include_all_pages_with_repeated_table(tmp_path):
    project = _fixture.make_project(tmp_path, panel_count=8)
    pages = compute_layout(project)["pages"]
    path = export.export_pptx(project, tmp_path, tmp_path / "pages.pptx", merge=False)
    with ZipFile(path) as package:
        for i in range(len(pages)):
            slide = ET.fromstring(package.read(f"ppt/slides/slide{i + 1}.xml"))
            assert slide.find(".//a:tbl", NS) is not None
    first = export.export_png(project, tmp_path, tmp_path / "pages.png", dpi=72)
    paths = [first] + [tmp_path / f"pages-p{i + 1}.png" for i in range(1, len(pages))]
    for i, path in enumerate(paths):
        with Image.open(path) as image:
            assert image.size == (960, 540)
            metadata = json.loads(image.info["wb_output"])
            assert metadata["page_index"] == i
            assert metadata["page_count"] == len(pages)


def test_png_without_cjk_font_still_writes_and_reports(project, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "_find_font", lambda: (None, False))
    with pytest.warns(RuntimeWarning, match="CJK font not found"):
        path = export.export_png(project, tmp_path, tmp_path / "fallback.png", dpi=72)
    with Image.open(path) as image:
        assert "CJK font not found" in json.loads(image.info["wb_output"])["font_warning"]


def test_failed_export_preserves_existing_file_and_removes_temporary(project, tmp_path, monkeypatch):
    path = tmp_path / "figure.pptx"
    path.write_bytes(b"existing output")

    def fail(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(export.os, "replace", fail)
    with pytest.raises(OSError, match="simulated write failure"):
        export.export_pptx(project, tmp_path, path, merge=False)
    assert path.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".figure.pptx.*.tmp"))


def test_excluded_panel_omitted_from_export_metadata_and_content(tmp_path):
    project = _fixture.make_project(tmp_path, panel_count=2)
    project.panels[0].included = False
    path = export.export_png(project, tmp_path, tmp_path / "filtered.png", dpi=72)
    with Image.open(path) as image:
        assert [p["panel_id"] for p in json.loads(image.info["wb_output"])["panels"]] == ["PANEL_B"]
    path = export.export_pptx(project, tmp_path, tmp_path / "filtered.pptx", merge=False)
    with ZipFile(path) as package:
        notes = package.read("ppt/notesSlides/notesSlide1.xml").decode()
        assert "PANEL_A:" not in notes and "PANEL_B:" in notes


def test_sample_gap_stays_blank_and_borderless_in_png_and_pptx(project, tmp_path):
    project.panels[0].display_geometry.lane_layout.spans = [(0, .2), (.2, .4), (.6, 1)]
    layout = compute_layout(project)
    table = next(e for e in layout['pages'][0]['elements'] if e['kind'] == 'table')
    path = export.export_png(project, tmp_path, tmp_path / 'gap.png', dpi=100)
    pixels = np.asarray(Image.open(path).convert('RGB'))
    x = round((table['sample_x'] + table['sample_w'] * .5) * 100)
    y0, y1 = round(table['y'] * 100), round((table['y'] + table['h']) * 100)
    assert np.all(pixels[y0:y1 + 1, x] == 255)
    path = export.export_pptx(project, tmp_path, tmp_path / 'gap.pptx', merge=False)
    with ZipFile(path) as package:
        slide = ET.fromstring(package.read('ppt/slides/slide1.xml'))
        cells = slide.findall('.//a:tbl/a:tr/a:tc', NS)
        gap = cells[3]  # label + two samples precede the gap
        assert not gap.findall('.//a:t', NS) or all(e.text in (None, '') for e in gap.findall('.//a:t', NS))
        assert len(gap.findall('.//a:tcPr/*/a:noFill', NS)) == 4
        assert len(cells) == 5  # label + three samples + one physical gap
