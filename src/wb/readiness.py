"""Shared final-output checks for CLI, HTTP and exporters."""

from wb.rendering import MissingTransform, exposure_to_reference, frame_for_capture


def output_readiness(project):
    blockers = []
    panels = [p for p in project.panels if p.included]
    for panel in panels:
        region = next(r for r in project.membrane_regions if r.region_id == panel.region_id)
        reasons = []
        if panel.review_status == "proposed":
            reasons.append("panel_proposed")
        if region.region_assignment_status != "confirmed" or not region.image_set_id or not region.geometry:
            reasons.append("region_assignment_unconfirmed")
        if not panel.selected_exposure_id:
            reasons.append("selected_exposure_missing")
        elif panel.image_set_id or region.image_set_id:
            image_set = next(
                (s for s in project.image_sets if s.image_set_id == (panel.image_set_id or region.image_set_id)), None
            )
            if not image_set or panel.selected_exposure_id not in image_set.capture_ids:
                reasons.append("selected_exposure_outside_image_set")
            elif panel.display_geometry:
                try:
                    exposure_to_reference(
                        project, panel.selected_exposure_id, panel.display_geometry.reference_frame_id
                    )
                    frame = frame_for_capture(project, panel.selected_exposure_id)
                    transform = next(
                        (
                            t
                            for t in project.transforms
                            if t.kind == "exposure_to_reference"
                            and t.from_frame_id == frame.frame_id
                            and t.to_frame_id == panel.display_geometry.reference_frame_id
                        ),
                        None,
                    )
                    if transform and transform.status == "candidate":
                        reasons.append("exposure_transform_candidate")
                except MissingTransform:
                    reasons.append("selected_exposure_missing_transform")
        if (
            not panel.display_geometry
            or not panel.display_geometry.crop_rect
            or not panel.display_geometry.target_width_px
        ):
            reasons.append("display_geometry_unset")
        if panel.display_geometry:
            transform = next(
                (
                    t
                    for t in project.transforms
                    if t.transform_id == panel.display_geometry.reference_to_rectified_transform_id
                ),
                None,
            )
            if transform and transform.status == "candidate":
                reasons.append("rectification_transform_candidate")
        if not region.lane_count:
            reasons.append("lane_count_unset")
        if panel.sample_group_id:
            group = next((g for g in project.sample_groups if g.sample_group_id == panel.sample_group_id), None)
            if (
                not group
                or group.lane_count != region.lane_count
                or len(group.samples) != region.lane_count
                or any(not s.name for s in group.samples)
            ):
                reasons.append("sample_order_unconfirmed")
        if panel.marker_registration_id:
            reg = next(
                (r for r in project.marker_registrations if r.marker_registration_id == panel.marker_registration_id),
                None,
            )
            transform = next(
                (
                    t
                    for t in project.transforms
                    if reg and t.transform_id == reg.transform_id and t.kind == "marker_to_reference"
                ),
                None,
            )
            if transform and transform.status == "candidate":
                reasons.append("marker_transform_candidate")
            if (
                not reg
                or reg.status != "confirmed"
                or not reg.marker_roi
                or not transform
                or transform.status != "confirmed"
            ):
                reasons.append("marker_registration_unconfirmed")
        if panel.label == "Actin" and panel.control_for_panel_ids and panel.review_status != "confirmed":
            reasons.append("control_relationship_unconfirmed")
        if any(f.target_id in (panel.panel_id, region.region_id) for f in project.review_flags):
            reasons.append("source_change_requires_review")
        blockers.extend({"panel_id": panel.panel_id, "reason": reason} for reason in reasons)
    if not panels:
        blockers.append({"panel_id": None, "reason": "no_panels"})
    return {"final_allowed": not blockers, "blocking_items": blockers}


REASON_TEXT = {
    "panel_proposed": "段が未確定",
    "exposure_transform_candidate": "採用露光の位置合わせが候補",
    "rectification_transform_candidate": "回転の位置合わせが候補",
    "marker_transform_candidate": "マーカーの位置合わせが候補",
    "region_assignment_unconfirmed": "領域の割り当てが未確認",
    "selected_exposure_missing": "採用露光が未選択",
    "selected_exposure_outside_image_set": "採用露光が領域の撮影系列に含まれない",
    "selected_exposure_missing_transform": "採用露光の位置合わせが未確認",
    "display_geometry_unset": "傾きとcropが未設定",
    "lane_count_unset": "レーン数が未設定",
    "sample_order_unconfirmed": "サンプル順が未確認",
    "marker_registration_unconfirmed": "マーカーの位置合わせが未確認",
    "control_relationship_unconfirmed": "対照関係が未確認",
    "source_change_requires_review": "元ファイルが変わったため再確認が必要",
    "no_panels": "パネルがありません",
}
