"""Small RGB TIFF project exercising layout and both export formats."""

import hashlib
from pathlib import Path

import numpy as np
import tifffile

from wb import project as projects
from wb.model import DisplayGeometry, MarkerRegistration, Project, SampleGroup, Transform


def make_project(root: Path, panel_count: int = 1) -> Project:
    records = {"sources": [], "frames": [], "captures": [], "image_sets": [
        {"image_set_id": "SET", "capture_ids": ["C1", "C2"], "reference_frame_id": "F1"},
    ]}
    for key, shade in (("1", 120), ("2", 30), ("M", 70)):
        pixels = np.full((60, 80, 3), 255, dtype=np.uint8)
        pixels[13:17, 5:15 if key == "M" else 65] = shade
        path = root / f"source-{key}.tif"
        tifffile.imwrite(path, pixels, photometric="rgb")
        data = path.read_bytes()
        records["sources"].append({
            "source_asset_id": f"S{key}", "relative_path": path.name,
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
            "representation_kind": "presentation_export_candidate", "quant_eligibility": "presentation_only",
            "image": {"width": 80, "height": 60, "dtype": "uint8", "channels": "RGB"},
        })
        records["frames"].append({
            "frame_id": f"F{key}", "source_asset_id": f"S{key}", "width": 80, "height": 60,
            "page_index": 0, "channel_selection": "RGB_display",
        })
        if key != "M":
            records["captures"].append({"capture_id": f"C{key}", "presentation_source_asset_id": f"S{key}"})
    project = projects.new_project("export-fixture", records)
    identity = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    project.transforms = [Transform(
        transform_id=tid, kind=kind, from_frame_id=source, to_frame_id=destination,
        matrix=identity, status="confirmed", evidence="synthetic aligned sources",
    ) for tid, kind, source, destination in (
        ("TR_EXPOSURE", "exposure_to_reference", "F2", "F1"),
        ("TR_RECT", "reference_to_rectified", "F1", "RECT"),
        ("TR_MARKER", "marker_to_reference", "FM", "F1"),
    )]
    project.sample_groups = [SampleGroup(
        sample_group_id="GROUP", lane_count=3, samples=[{"name": "control"}, {"name": ""}, {"name": None}],
    )]
    for i in range(panel_count):
        name = chr(ord("A") + i)
        project = projects.add_region(project, name, name)
        panel = project.panels[-1]
        panel.image_set_id = "SET"
        panel.sample_group_id = "GROUP"
        panel.selected_exposure_id = "C2"
        panel.marker_registration_id = f"MARKER_{i}"
        panel.display_geometry = DisplayGeometry(
            reference_frame_id="F1", rectified_frame_id="RECT", reference_to_rectified_transform_id="TR_RECT",
            crop_rect={"x0": 20, "y0": 10, "x1": 70, "y1": 20}, target_width_px=100,
            lane_layout={"lane_count": 3}, geometry_revision=4,
        )
        project.marker_registrations.append(MarkerRegistration(
            marker_registration_id=f"MARKER_{i}", membrane_region_id=panel.region_id,
            transform_id="TR_MARKER", marker_roi={
                "frame_id": "FM", "polygon": [[5, 0], [15, 0], [15, 60], [5, 60]],
            }, status="confirmed",
        ))
    projects.save_project(project, root / "fixture.wblot", expected_revision=project.project_revision)
    return projects.load_project(root / "fixture.wblot")
