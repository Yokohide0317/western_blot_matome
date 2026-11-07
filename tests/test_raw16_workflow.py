"""A raw16-only ChemiDoc folder must produce inspectable drafts without a marker."""

import hashlib
import json
from zipfile import ZipFile

import numpy as np
from PIL import Image
import pytest
import tifffile

from wb import api
from wb.ingest import scan_archive, scan_folder
from wb.rendering import load_source_pixels, render_panel


@pytest.fixture
def raw_folder(tmp_path):
    folder = tmp_path / "raw-input"
    series = folder / "series"
    series.mkdir(parents=True)
    for exposure in (1, 2, 3):
        pixels = np.full((420, 640), 500, np.uint16)
        for x in range(100, 510, 50):
            pixels[84:96, x:x + 22] = exposure * 5000
            pixels[294:306, x:x + 22] = (8000, 24000, 65535)[exposure - 1]
        tifffile.imwrite(series / f"{exposure}.000s(Chemiluminescence).raw16.tif", pixels,
                         photometric="miniswhite")
    return folder


def test_raw16_import_preserves_pixels_and_links_scn(raw_folder):
    scn = raw_folder / "series" / "1.000s.scn"
    scn.write_bytes(b'<!DOCTYPE XML><root><size_pix width="640" height="420"/>'
                    b'<scan_attributes><exposure_time value="1.0"/></scan_attributes></root>')
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw_folder.rglob("*") if p.is_file()}
    project = api.import_project(raw_folder)
    assert len(project.captures) == len(project.frames) == 3
    first = project.captures[0]
    assert first.exposure_s == 1
    assert first.exposure_source.startswith("scn.")
    assert first.native_source_asset_id == "RAW16_UNPAIRED001_SCN"
    assert first.presentation_source_asset_id == first.analysis_source_asset_id
    pixels = load_source_pixels(project, raw_folder, first.presentation_source_asset_id)
    assert pixels.dtype == np.uint8 and pixels.shape == (420, 640, 3)
    assert pixels[90, 110, 0] == 255 - round(5000 / 65535 * 255)
    assert pixels[0, 0, 0] == 255 - round(500 / 65535 * 255)
    last = load_source_pixels(project, raw_folder, project.captures[-1].presentation_source_asset_id)
    assert last[300, 110].tolist() == [255, 0, 0]
    assert all(s.quant_eligibility == "unverified" for s in project.sources)
    assert api.import_project(raw_folder).project_revision == project.project_revision
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())


def test_raw_folder_and_archive_have_the_same_captures(raw_folder, tmp_path):
    archive = tmp_path / "input.zip"
    with ZipFile(archive, "w") as z:
        for path in raw_folder.rglob("*.tif"):
            z.write(path, raw_folder.name + "/" + path.relative_to(raw_folder).as_posix())
    a, b = scan_folder(raw_folder).to_project_records(), scan_archive(archive).to_project_records()
    assert a["captures"] == b["captures"]
    assert a["frames"] == b["frames"]
    assert a["image_sets"] == b["image_sets"]


def test_markerless_propose_apply_and_export(raw_folder):
    initial = api.import_project(raw_folder)
    proposal = api.propose(raw_folder)
    assert len(proposal["groups"]) == 1
    group = proposal["groups"][0]
    assert group["marker_frame_id"] is None
    assert len(group["membranes"]) == 2
    assert proposal == api.propose(raw_folder)
    project = api.apply_proposal(raw_folder, proposal, initial.project_revision)
    assert api.apply_proposal(raw_folder, proposal, project.project_revision).project_revision == project.project_revision
    assert not project.marker_registrations
    assert all(t.status == "candidate" for t in project.transforms)
    assert all(r.lane_count is None for r in project.membrane_regions)
    assert all(p.review_status == "proposed" for p in project.panels)
    captures = {c.capture_id: c for c in project.captures}
    assert [captures[p.selected_exposure_id].exposure_s for p in project.panels] == [3, 2]
    assert all(render_panel(project, raw_folder, p.panel_id, p.selected_exposure_id).png for p in project.panels)
    status = api.status(raw_folder)
    assert not status["readiness"]["final_allowed"]
    assert status["warnings"]
    pptx = api.export_project(raw_folder, "pptx")["path"]
    png = api.export_project(raw_folder, "png")["path"]
    with ZipFile(pptx) as z:
        notes = z.read("ppt/notesSlides/notesSlide1.xml").decode()
        assert "linear_uint16_full_range" in notes
        assert "下書き" not in z.read("ppt/slides/slide1.xml").decode()
        media = [z.read(n) for n in z.namelist() if n.startswith("ppt/media/")]
        for panel in project.panels:
            assert render_panel(project, raw_folder, panel.panel_id, panel.selected_exposure_id).png in media
    with Image.open(png) as image:
        metadata = json.loads(image.info["wb_output"])
        assert "final" not in metadata and "mode" not in metadata
        assert len(metadata["display_mappings"]) == 3
    assert api.validate(raw_folder)["source_issues"] == []


def test_raw16_preview_cache_tracks_the_display_mapping(raw_folder):
    from wb.rendering import render_overview

    project = api.import_project(raw_folder)
    frame = project.frames[0]
    first, _ = render_overview(project, raw_folder, frame.frame_id)
    source = next(s for s in project.sources if s.source_asset_id == frame.source_asset_id)
    source.display_mapping = {**source.display_mapping, "invert": False}
    second, _ = render_overview(project, raw_folder, frame.frame_id)
    assert first.cache_path != second.cache_path
    assert first.png != second.png


def test_markerless_two_positive_bands_are_retained(tmp_path):
    pixels = np.full((420, 640), 500, np.uint16)
    pixels[220:236, 100:122] = 24000
    pixels[223:239, 150:172] = 18000
    tifffile.imwrite(tmp_path / '60.000s(Chemiluminescence).raw16.tif', pixels,
                     photometric='miniswhite')
    api.import_project(tmp_path)
    proposal = api.propose(tmp_path)
    membranes = [m for g in proposal['groups'] for m in g['membranes']]
    assert len(membranes) == 1
    assert membranes[0]['row']['evidence']['strong_band_component_count'] >= 1
    assert membranes[0]['row']['evidence']['score_components']['lane_peak_count'] < 3


def test_new_color_source_is_reported_and_refresh_keeps_a_backup(raw_folder):
    initial = api.import_project(raw_folder)
    proposal = api.propose(raw_folder)
    initial = api.apply_proposal(raw_folder, proposal, initial.project_revision)
    project_file = api.P.locate(raw_folder)
    before = project_file.read_bytes()
    color = np.full((420, 640, 3), 220, dtype=np.uint8)
    tifffile.imwrite(raw_folder / '0.200s(Colorimetric).tif', color, photometric='rgb')
    tifffile.imwrite(raw_folder / '0.200s(Colorimetric).raw16.tif',
                     np.full((840, 1280), 500, np.uint16), photometric='miniswhite')
    assert api.import_project(raw_folder) == initial
    assert any('未取り込み' in w for w in api.status(raw_folder)['warnings'])
    refreshed = api.import_project(raw_folder, refresh=True)
    assert len(refreshed.captures) == 4
    assert sum(c.modality == 'colorimetric' for c in refreshed.captures) == 1
    assert refreshed.project_id == initial.project_id
    assert refreshed.project_revision == initial.project_revision + 1
    assert not refreshed.panels and not refreshed.transforms
    assert (raw_folder / refreshed.refresh_backup).read_bytes() == before
    assert api.unimported_sources(refreshed, raw_folder) == []
    assert api.validate(raw_folder)['source_issues'] == []
    assert api.import_project(raw_folder) == refreshed
