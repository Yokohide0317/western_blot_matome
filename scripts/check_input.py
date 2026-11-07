"""Run the real import-to-export path and retain a source-checked receipt.

Usage: uv run python scripts/check_input.py /path/to/ChemiDoc-folder
Existing Selection edits are retained; exports use the current saved state.
"""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from wb import api, project as P
from wb.rendering import render_panel


def check_input(folder):
    project = api.import_project(folder)
    root = P.locate(folder).parent
    proposal_path = P.output_dir(root) / "proposal.json"
    if not project.panels:
        proposal = api.propose(root)
        proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        project = api.apply_proposal(root, proposal, project.project_revision)
    if not any(p.included for p in project.panels):
        raise RuntimeError("図に含める段がありません。領域を指定してください。")
    validation = api.validate(root)
    if validation["source_issues"]:
        raise RuntimeError(f"元画像の検証に失敗しました: {validation['source_issues']}")
    png = api.export_project(root, "png")
    pptx = api.export_project(root, "pptx")
    with Image.open(png["path"]) as image:
        image.load()
        raster_metadata = json.loads(image.info["wb_output"])
    if raster_metadata["project_revision"] != project.project_revision:
        raise RuntimeError("PNGの採用情報がプロジェクトと一致しません")
    panels = []
    with ZipFile(pptx["path"]) as package:
        media = {package.read(n) for n in package.namelist() if n.startswith("ppt/media/")}
        for panel in sorted(project.panels, key=lambda p: p.display_order):
            capture = next(c for c in project.captures if c.capture_id == panel.selected_exposure_id)
            region = next(r for r in project.membrane_regions if r.region_id == panel.region_id)
            candidate = next((c for c in region.band_row_candidates
                              if c.candidate_id == panel.selected_band_row_candidate_id), None)
            rendered = render_panel(project, root, panel.panel_id, panel.selected_exposure_id)
            if panel in project.included_panels() and rendered.png not in media:
                raise RuntimeError(f"PPTXに採用画像がありません: {panel.panel_id}")
            panels.append({"panel_id": panel.panel_id, "label": panel.label, "included": panel.included,
                "exposure_s": capture.exposure_s, "source_asset_id": capture.presentation_source_asset_id,
                "crop": panel.display_geometry.crop_rect.model_dump(),
                "saturated_pixels_in_candidate_crop": candidate.red_pixels_in_crop_by_capture.get(capture.capture_id)
                    if candidate else None,
                "preview": str(rendered.cache_path)})
    source_issues = P.verify_sources(project, root)
    if source_issues:
        raise RuntimeError(f"出力後に元画像が変わっています: {source_issues}")
    report = {"project": str(P.locate(root)), "project_revision": project.project_revision,
        "capture_count": len(project.captures), "source_count": len(project.sources),
        "image_sets": [{"id": s.image_set_id, "capture_count": len(s.capture_ids)} for s in project.image_sets],
        "source_issues": source_issues, "panels": panels, "status": api.status(root),
        "exports": {"png": png["path"], "pptx": pptx["path"]},
        "checks": {"png_metadata_matches_project": True, "pptx_adopted_pixels_match_renderer": True,
                   "source_hashes_unchanged": True}}
    report_path = P.output_dir(root) / "input_check.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"report": str(report_path), **report["exports"], "panels": panels}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    print(json.dumps(check_input(args.folder), ensure_ascii=False, indent=2))
