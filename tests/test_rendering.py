import hashlib
import numpy as np
import pytest
import tifffile

from wb import api, project as P
from wb.rendering import MissingTransform, exposure_to_reference, render_panel


@pytest.fixture
def small_project(tmp_path):
    records = {"sources": [], "frames": [], "captures": [], "image_sets": []}
    for n in (1, 2, 3):
        path = tmp_path / f"{n}.tif"
        tifffile.imwrite(path, np.full((40, 50, 3), n * 40, dtype=np.uint8), photometric="rgb")
        b = path.read_bytes()
        records["sources"].append(
            {
                "source_asset_id": f"S{n}",
                "relative_path": path.name,
                "sha256": hashlib.sha256(b).hexdigest(),
                "size_bytes": len(b),
                "representation_kind": "presentation_export_candidate",
                "image": {"width": 50, "height": 40, "dtype": "uint8", "channels": "RGB"},
                "quant_eligibility": "presentation_only",
            }
        )
        records["frames"].append(
            {
                "frame_id": f"F{n}",
                "source_asset_id": f"S{n}",
                "width": 50,
                "height": 40,
                "page_index": 0,
                "channel_selection": "RGB_display",
            }
        )
        records["captures"].append(
            {"capture_id": f"C{n}", "presentation_source_asset_id": f"S{n}", "modality": "chemiluminescence"}
        )
    records["image_sets"] = [{"image_set_id": "SET", "capture_ids": ["C1", "C2", "C3"]}]
    p = P.new_project("test", records)
    p = P.add_region(P.add_region(p, "K", "TargetC"), "L", "TargetC")
    p = P.save_project(p, P.locate(tmp_path, create=True), p.project_revision)
    return tmp_path, p


def test_alignment_render_cache_and_independence(small_project):
    path, p = small_project
    p = api.confirm_identity_alignment(path, "SET", "F1", "visually checked", p.project_revision)
    p = api.update_region(
        path, "REGION_K", "SET", "F1", [[5, 5], [40, 5], [40, 30], [5, 30]], 3, "confirmed", p.project_revision
    )
    p = api.update_display_geometry(
        path,
        "PANEL_K",
        "F1",
        10,
        [25, 20],
        {"x0": 10, "y0": 10, "x1": 35, "y1": 25},
        60,
        {"lane_count": 3, "centers_x": [15, 22, 29]},
        p.project_revision,
    )
    images = [render_panel(p, path, "PANEL_K", f"C{i}") for i in (1, 2, 3)]
    assert len({(i.width, i.height) for i in images}) == 1
    before = images[0].cache_path.stat().st_mtime_ns
    p = api.set_selected_exposure(path, "PANEL_K", "C2", p.project_revision)
    assert render_panel(p, path, "PANEL_K", "C1").cache_path.stat().st_mtime_ns == before
    assert next(r for r in p.membrane_regions if r.user_label == "L").geometry is None
    p = api.update_display_geometry(
        path, "PANEL_K", "F1", 10, [25, 20], {"x0": 10, "y0": 10, "x1": 35, "y1": 25}, 60, {}, p.project_revision
    )
    assert render_panel(p, path, "PANEL_K", "C1").cache_path != images[0].cache_path


def test_missing_transform(small_project):
    _, p = small_project
    with pytest.raises(MissingTransform):
        exposure_to_reference(p, "C2", "F1")
