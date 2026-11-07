import time
from pathlib import Path

import pytest

from wb import api
from wb.geometry import OutOfFrameError
from wb.project import RevisionConflict
from test_rendering import small_project


SEED = Path(__file__).resolve().parents[1] / "instructions/western_blot_project_seed.json"


def test_real_import_idempotent_and_changed_source(input_zip, tmp_path):
    start = time.monotonic()
    p = api.import_project(input_zip, tmp_path, seed_path=SEED)
    assert len(p.sources) == 100 and len(p.image_sets) == 5
    assert [r.user_label for r in p.membrane_regions] == ["I", "J", "K", "L"]
    assert all(r.geometry is None for r in p.membrane_regions)
    assert api.import_project(input_zip, tmp_path, seed_path=SEED) == p
    source = tmp_path / p.sources[0].relative_path
    source.chmod(0o644)
    data = bytearray(source.read_bytes())
    data[-1] ^= 1
    source.write_bytes(data)
    assert api.validate(tmp_path)["source_issues"][0]["source_asset_id"] == p.sources[0].source_asset_id
    print(f"real import and validation: {time.monotonic() - start:.2f}s")


def test_bounds_and_revision(small_project):
    path, p = small_project
    with pytest.raises(OutOfFrameError):
        api.update_display_geometry(
            path, "PANEL_K", "F1", 0, [0, 0], {"x0": -1, "y0": 0, "x1": 5, "y1": 5}, 10, {}, p.project_revision
        )
    with pytest.raises(RevisionConflict):
        api.set_selected_exposure(path, "PANEL_K", "C1", p.project_revision - 1)


def test_real_geometry_all_exposures(input_zip, tmp_path):
    from wb.rendering import render_panel

    start = time.monotonic()
    p = api.import_project(input_zip, tmp_path, seed_path=SEED)
    image_set = next(s for s in p.image_sets if s.image_set_id == "S02")
    capture = next(c for c in p.captures if c.capture_id == image_set.capture_ids[0])
    frame = next(f for f in p.frames if f.source_asset_id == capture.presentation_source_asset_id)
    p = api.confirm_identity_alignment(tmp_path, "S02", frame.frame_id, "synthetic test alignment", p.project_revision)
    p = api.update_region(
        tmp_path,
        "REGION_K",
        "S02",
        frame.frame_id,
        [[400, 400], [800, 400], [800, 700], [400, 700]],
        3,
        "confirmed",
        p.project_revision,
    )
    p = api.update_display_geometry(
        tmp_path,
        "PANEL_K",
        frame.frame_id,
        5,
        [600, 550],
        {"x0": 400, "y0": 400, "x1": 800, "y1": 700},
        240,
        {},
        p.project_revision,
    )
    from wb.geometry import crop_polygon_in_source
    from wb.rendering import display_parts, exposure_to_reference

    panel = next(x for x in p.panels if x.panel_id == "PANEL_K")
    assert all(render_panel(p, tmp_path, panel.panel_id, cid).png for cid in image_set.capture_ids)
    d, crop = display_parts(p, panel)
    polygon = crop_polygon_in_source(exposure_to_reference(p, image_set.capture_ids[0], frame.frame_id), d, crop)
    assert polygon[0][1] != polygon[1][1]
    first = render_panel(p, tmp_path, "PANEL_K", image_set.capture_ids[0])
    timestamp = first.cache_path.stat().st_mtime_ns
    p = api.set_selected_exposure(tmp_path, "PANEL_K", image_set.capture_ids[1], p.project_revision)
    assert render_panel(p, tmp_path, "PANEL_K", image_set.capture_ids[0]).cache_path.stat().st_mtime_ns == timestamp
    p = api.update_display_geometry(
        tmp_path,
        "PANEL_K",
        frame.frame_id,
        5,
        [600, 550],
        {"x0": 400, "y0": 400, "x1": 800, "y1": 700},
        240,
        {},
        p.project_revision,
    )
    assert render_panel(p, tmp_path, "PANEL_K", image_set.capture_ids[0]).cache_path != first.cache_path
    print(f"real geometry and bundle: {time.monotonic() - start:.2f}s")


def test_real_unassigned_overviews(input_zip, tmp_path):
    p = api.import_project(input_zip, tmp_path, seed_path=SEED)
    from wb.readiness import output_readiness

    assert all(panel.display_geometry is None for panel in p.panels)
    assert len(output_readiness(p)["blocking_items"]) >= 16


def test_duplicate_reorder_and_lane_updates(small_project):
    from wb.model import BandRowCandidate
    from wb import project as P

    root, p = small_project
    p = api.confirm_identity_alignment(root, "SET", "F1", "確認", p.project_revision)
    p = api.update_region(root, "REGION_K", "SET", "F1", [[1, 1], [45, 1], [45, 35]], None, "proposed", p.project_revision)
    p = api.update_display_geometry(root, "PANEL_K", "F1", 0, [0, 0], {"x0": 5, "y0": 5, "x1": 40, "y1": 15}, 70, {"centers_x": [10, 20]}, p.project_revision)
    panel = next(x for x in p.panels if x.panel_id == "PANEL_K")
    panel.selected_exposure_id = "C1"
    region = next(x for x in p.membrane_regions if x.region_id == panel.region_id)
    region.band_row_candidates = [BandRowCandidate(candidate_id=f"row{i}", crop_rect_rectified={"x0": 5, "x1": 40, "y0": y, "y1": y + 5}, band_row_y_rectified=y, selected_exposure_id="C2", exposure_reason="test", score=i) for i, y in [(1, 12), (2, 20)]]
    p = P.save_project(p, P.locate(root), p.project_revision)
    p = api.duplicate_panel(root, "PANEL_K", p.project_revision)
    copy = next(x for x in p.panels if x.panel_id.startswith("PANEL_K_DUP"))
    assert copy.selected_band_row_candidate_id == panel.selected_band_row_candidate_id
    assert copy.selected_exposure_id == panel.selected_exposure_id
    assert copy.display_geometry.crop_rect == panel.display_geometry.crop_rect
    assert copy.display_geometry.reference_to_rectified_transform_id != panel.display_geometry.reference_to_rectified_transform_id
    p = api.adjust(root, copy.panel_id, p.project_revision, crop_top=6)
    original = next(x for x in p.panels if x.panel_id == "PANEL_K")
    assert original.display_geometry.crop_rect.y0 == 5
    p = api.reorder_panels(root, ["PANEL_L", copy.panel_id], p.project_revision)
    assert [x["panel_id"] for x in api.panel_summaries(p)] == ["PANEL_L", copy.panel_id, "PANEL_K"]
    assert all(x.display_order_status == "confirmed" for x in p.panels)
    with pytest.raises(ValueError):
        api.reorder_panels(root, [copy.panel_id, copy.panel_id], p.project_revision)
    for invalid in (True, 0, 2.5):
        with pytest.raises(ValueError):
            api.set_lanes(root, invalid, p.project_revision)
    p = api.set_lanes(root, 3, p.project_revision, panel_id="PANEL_K", sample_names=[" control ", ""])
    group = next(g for g in p.sample_groups if g.sample_group_id == "SG_PANEL_K")
    assert [s.name for s in group.samples] == ["control", None, None]
    assert next(x for x in p.panels if x.panel_id == "PANEL_K").display_geometry.lane_layout.centers_x == []
    p = api.set_lanes(root, 2, p.project_revision, panel_id="PANEL_K")
    assert [s.name for s in p.sample_groups[-1].samples] == ["control", None]


def test_lane_boundaries_equal_reset_retention_isolation_and_revision(small_project):
    root, p = small_project
    p = api.update_display_geometry(root, "PANEL_K", "F1", 0, [0, 0], {"x0": 5, "y0": 5, "x1": 40, "y1": 15}, 70, {}, p.project_revision)
    p = api.update_display_geometry(root, "PANEL_L", "F1", 0, [0, 0], {"x0": 5, "y0": 18, "x1": 40, "y1": 28}, 70, {}, p.project_revision)
    layout = lambda proj, pid: next(x for x in proj.panels if x.panel_id == pid).display_geometry.lane_layout

    # Setting a lane count seeds equal boundaries on the target panel only.
    p = api.set_lanes(root, 2, p.project_revision, panel_id="PANEL_K")
    assert layout(p, "PANEL_K").boundaries == [0, 0.5, 1]
    assert layout(p, "PANEL_L").boundaries == []

    # Custom boundaries persist across a save/reload and stay per panel.
    p = api.set_lane_boundaries(root, "PANEL_K", [0, 0.3, 1], p.project_revision)
    assert layout(api._load(root), "PANEL_K").boundaries == [0, 0.3, 1]
    assert layout(api._load(root), "PANEL_L").boundaries == []

    # The same lane count keeps custom boundaries; a new count resets to equal.
    p = api.set_lanes(root, 2, p.project_revision, panel_id="PANEL_K")
    assert layout(p, "PANEL_K").boundaries == [0, 0.3, 1]
    p = api.set_lanes(root, 4, p.project_revision, panel_id="PANEL_K")
    assert layout(p, "PANEL_K").boundaries == [0, 0.25, 0.5, 0.75, 1]

    # Duplicating a row carries the relative boundaries over.
    p = api.set_lane_boundaries(root, "PANEL_K", [0, 0.4, 0.6, 0.8, 1], p.project_revision)
    p = api.duplicate_panel(root, "PANEL_K", p.project_revision)
    copy = next(x for x in p.panels if x.panel_id.startswith("PANEL_K_DUP"))
    assert copy.display_geometry.lane_layout.boundaries == [0, 0.4, 0.6, 0.8, 1]
    assert layout(p, "PANEL_K").boundaries == [0, 0.4, 0.6, 0.8, 1]

    # Invalid boundary sets and stale revisions are rejected.
    for bad in ([0, 0.5, 1], [0, 0.2, 0.4, 0.6, 0.8, 1], [0, 0.25, 0.5, 0.75, 1.5], [0, 0.9, 0.8, 0.85, 1], [0, 0.2, 0.21, 0.6, 1]):
        with pytest.raises(ValueError):
            api.set_lane_boundaries(root, "PANEL_K", bad, p.project_revision)
    with pytest.raises(RevisionConflict):
        api.set_lane_boundaries(root, "PANEL_K", [0, 0.3, 0.5, 0.7, 1], p.project_revision - 1)
    with pytest.raises(ValueError):
        api.set_lane_boundaries(root, "PANEL_L", [0, 1], p.project_revision)

    # Dense lane counts seed and validate equal boundaries (relaxed min spacing).
    p = api.set_lanes(root, 51, p.project_revision, panel_id="PANEL_K")
    assert layout(p, "PANEL_K").boundaries == pytest.approx([i / 51 for i in range(52)])
    assert layout(api._load(root), "PANEL_K").lane_count == 51


def test_pptx_exports_get_timestamped_never_overwriting_names(small_project, monkeypatch):
    import re

    root, p = small_project

    class Frozen:
        @classmethod
        def now(cls, tz=None):
            return cls()

        def astimezone(self):
            return self

        def strftime(self, fmt):
            return "20260704-101112-123456+0900"

    monkeypatch.setattr(api, "datetime", Frozen)
    first = Path(api.export_project(root, "pptx")["path"])
    second = Path(api.export_project(root, "pptx")["path"])
    assert re.search(r"_20260704-101112-123456\+0900\.pptx$", first.name)
    assert second.name == first.name.replace(".pptx", "-1.pptx")
    assert first.is_file() and second.is_file() and first.read_bytes() != b""
    first_bytes = first.read_bytes()

    # A failed export removes only its own placeholder.
    from wb import export as exp

    real = exp.export_pptx
    monkeypatch.setattr(exp, "export_pptx", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError, match="boom"):
        api.export_project(root, "pptx")
    names = {x.name for x in first.parent.iterdir()}
    assert first.name in names and second.name in names
    assert not any(n.endswith("-2.pptx") for n in names)
    assert first.read_bytes() == first_bytes
    monkeypatch.setattr(exp, "export_pptx", real)

    # Final uses a different basename; PNG keeps the historical fixed name.
    monkeypatch.setattr(api, "output_readiness", lambda p: {"final_allowed": True, "blocking_items": []})
    final = Path(api.export_project(root, "pptx")["path"])
    assert "_final_" not in final.name and final.name != first.name
    png = Path(api.export_project(root, "png")["path"])
    assert png.name == f"{root.name}.png"
    assert Path(api.export_project(root, "png")["path"]) == png  # PNG keeps overwriting


def test_approve_all_requires_evidence_and_one_revision(small_project):
    root, p = small_project
    p = api.confirm_identity_alignment(root, "SET", "F1", "確認", p.project_revision)
    p = api.update_region(root, "REGION_K", "SET", "F1", [[1, 1], [45, 1], [45, 35]], None, "proposed", p.project_revision)
    p = api.update_display_geometry(root, "PANEL_K", "F1", 0, [0, 0], {"x0": 5, "y0": 5, "x1": 40, "y1": 15}, 70, {}, p.project_revision)
    p = api.set_selected_exposure(root, "PANEL_K", "C2", p.project_revision)
    p = api.set_panel_included(root, "PANEL_L", False, p.project_revision)
    p = api.set_lanes(root, 2, p.project_revision, sample_names=["a", "b"])
    p.transforms[1].status = "candidate"
    from wb import project as P
    p = P.save_project(p, P.locate(root), p.project_revision)
    with pytest.raises(ValueError, match="根拠"):
        api.approve_all(root, {}, p.project_revision)
    revision = p.project_revision
    p = api.approve_all(root, {"review": "確認"}, revision)
    assert p.project_revision == revision + 1
    assert next(r for r in p.membrane_regions if r.region_id == "REGION_K").region_assignment_status == "confirmed"
    assert next(x for x in p.panels if x.panel_id == "PANEL_L").review_status != "confirmed"
    assert all(t.status == "confirmed" for t in p.transforms)
    assert api.status(root)["readiness"]["final_allowed"]
    for kind in ("png", "pptx"):
        assert Path(api.export_project(root, kind)["path"]).is_file()


def test_manual_membrane_custom_selection_and_colorimetric_marker_on_approve(small_project):
    from wb import project as P
    from wb.rendering import render_marker, render_panel

    root, p = small_project
    p.image_sets[0].capture_ids = ["C1", "C2"]
    p.captures[2].modality = "colorimetric"
    p = P.save_project(p, P.locate(root), p.project_revision, "colorimetric_setup")

    before_ids = {panel.panel_id for panel in p.panels}
    p = api.add_manual_membrane(
        root,
        p.project_revision,
        rect={"x0": 6, "y0": 6, "x1": 44, "y1": 32},
        rotation_deg=2.5,
        user_label="M_NEW",
        label="TargetB",
    )
    created = next(panel for panel in p.panels if panel.panel_id not in before_ids)
    assert created.label == "TargetB"
    assert created.marker_registration_id is not None

    before_ids = {panel.panel_id for panel in p.panels}
    p = api.add_panel_selection(
        root,
        created.panel_id,
        {"x0": 10, "y0": 12, "x1": 42, "y1": 24},
        p.project_revision,
        label="TargetA",
    )
    added = next(panel for panel in p.panels if panel.panel_id not in before_ids)
    assert added.region_id == created.region_id
    assert added.label == "TargetA"
    assert added.display_geometry.crop_rect.y0 == 12 and added.display_geometry.crop_rect.y1 == 24

    # Even if marker_roi was absent before confirmation, approve_panel extracts & confirms it from colorimetric.
    added.marker_registration_id = None
    p.marker_registrations = [r for r in p.marker_registrations if r.membrane_region_id != added.region_id]
    p = P.save_project(p, P.locate(root), p.project_revision, "clear_marker")
    p = api.approve_panel(root, added.panel_id, {"confirmed_by": "test"}, p.project_revision)
    approved = next(panel for panel in p.panels if panel.panel_id == added.panel_id)
    assert approved.review_status == "confirmed"
    assert approved.marker_registration_id is not None
    reg = next(r for r in p.marker_registrations if r.marker_registration_id == approved.marker_registration_id)
    assert reg.status == "confirmed" and reg.marker_roi is not None
    blot = render_panel(p, root, approved.panel_id, approved.selected_exposure_id)
    marker = render_marker(p, root, approved.panel_id)
    assert marker["image"] is not None and marker["image"].height == blot.height and marker["image"].png


def test_lane_spans_preserve_gaps_and_do_not_add_a_sample(small_project):
    root, p = small_project
    p = api.update_display_geometry(root, 'PANEL_K', 'F1', 0, [0, 0],
        {'x0': 5, 'y0': 5, 'x1': 40, 'y1': 15}, 70, {}, p.project_revision)
    spans = [(i / 10, (i + 1) / 10) for i in (0, 1, 2, 4, 5, 6, 7, 8, 9)]
    p = api.set_lanes(root, 9, p.project_revision, panel_id='PANEL_K',
                      sample_names=[None] * 9, lane_spans=spans)
    layout = lambda project: next(x for x in project.panels if x.panel_id == 'PANEL_K').display_geometry.lane_layout
    assert layout(api._load(root)).spans == spans
    assert layout(p).lane_count == 9
    assert len(p.sample_groups[-1].samples) == 9
    assert all(s.name is None for s in p.sample_groups[-1].samples)
    p = api.set_lanes(root, 9, p.project_revision, panel_id='PANEL_K')
    assert layout(p).spans == spans
    with pytest.raises(ValueError, match='lane_spans'):
        api.set_lane_boundaries(root, 'PANEL_K', [i / 9 for i in range(10)], p.project_revision)
    for bad in (spans[:-1], [(0, .5), (.4, .8), (.8, 1)], [(0, 1.1)] * 9):
        with pytest.raises(ValueError):
            api.set_lanes(root, 9, p.project_revision, panel_id='PANEL_K', lane_spans=bad)
    assert layout(api._load(root)).spans == spans
    p = api.set_lanes(root, 3, p.project_revision, panel_id='PANEL_K')
    assert layout(p).spans == []
    assert layout(p).boundaries == pytest.approx([0, 1 / 3, 2 / 3, 1])


def test_figure_lane_scale_is_explicit_and_persisted(small_project):
    root, p = small_project
    assert p.figure_scale_mode == 'equal_lane_width'
    legacy = p.model_dump()
    legacy['figure_scale_mode'] = 'equal_width'
    assert type(p).model_validate(legacy).figure_scale_mode == 'equal_lane_width'
    p = api.set_figure_scale(root, 'equal_lane_width', p.project_revision)
    assert api._load(root).figure_scale_mode == 'equal_lane_width'
    for mode in ('equal_width', 'from_band_count'):
        with pytest.raises(ValueError):
            api.set_figure_scale(root, mode, p.project_revision)
    assert api._load(root).project_revision == p.project_revision
