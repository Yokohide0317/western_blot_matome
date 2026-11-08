"""Project operations shared by the CLI and editing interfaces."""

from dataclasses import asdict
from datetime import datetime
import hashlib
import itertools
import json
import os
from pathlib import Path

from wb import project as P
from wb.readiness import output_readiness, REASON_TEXT
from wb.geometry import Affine, Rect, require_within_frame
from wb.ingest import folder_files, scan_archive, scan_folder, materialize_archive, read_metadata_xlsx
from wb.model import DisplayGeometry, FramePolygon, Transform


def _load(project_dir):
    return P.load_project(P.locate(project_dir))


def _save(project_dir, project, expected_revision, operation, summary=""):
    return P.save_project(project, P.locate(project_dir, create=True), expected_revision, operation, summary)


def _get(items, key, value):
    item = next((x for x in items if getattr(x, key) == value), None)
    if item is None:
        raise P.UnknownId(f"{key}が見つかりません: {value!r}")
    return item


def unimported_sources(project, root):
    known = {s.relative_path for s in project.sources}
    return sorted(p.relative_to(root).as_posix() for p in folder_files(root)
                  if p.is_file() and p.suffix.lower() in ('.tif', '.tiff', '.scn')
                  and not any(part in ('wblot_output', '__MACOSX') or part.startswith('._')
                              for part in p.relative_to(root).parts)
                  and p.relative_to(root).as_posix() not in known)


def import_project(input_zip, project_dir=None, metadata_xlsx=None, seed_path=None, project_id=None, refresh=False):
    source = Path(input_zip).expanduser().absolute()
    if source.suffix.lower() == ".zip":
        P.require_local([source])
        root = materialize_archive(source, project_dir)
    else:
        root = source
        if project_dir is not None and Path(project_dir).absolute() != root:
            raise P.ProjectError("フォルダ入力はその場で初期化してください")
    path = P.locate(root, create=True)
    existing = None
    if path.exists():
        existing = P.load_project(path)
        issues = P.verify_sources(existing, root)
        if issues and not refresh:
            raise P.ProjectError("元画像が変更されています。既存の座標を再利用できません")
        if not refresh:
            P.output_dir(path)
            return existing
    project_dir = root
    ledger = scan_archive(source) if source.suffix.lower() == ".zip" else scan_folder(root)
    metadata = read_metadata_xlsx(metadata_xlsx) if metadata_xlsx else None
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8")) if seed_path else None
    project = P.new_project(
        project_id or (existing.project_id if existing else P.stable_id("PROJECT", ledger.archive_sha256)),
        ledger.to_project_records(), seed
    )
    if metadata:
        for row in metadata.panels:
            label = row.get("membrane_label")
            if not label:
                raise ValueError("メタデータにはmembrane_labelが必要です")
            project = P.add_region(project, str(label), row.get("label"))
            region = _get(project.membrane_regions, "user_label", str(label))
            panel = _get(project.panels, "region_id", region.region_id)
            region.image_set_id = row.get("image_set_id")
            if region.image_set_id:
                _get(project.image_sets, "image_set_id", region.image_set_id)
            region.lane_count = row.get("lane_count")
            panel.label = row.get("label") or panel.label
            panel.sample_group_id = row.get("sample_group_id")
            panel.display_order = row["display_order"]
    if P.verify_sources(project, root):
        raise P.ProjectError("展開した画像のSHA-256が台帳と一致しません")
    backup = None
    if existing:
        backups = P.output_dir(root) / 'backups'
        backups.mkdir(exist_ok=True)
        backup = backups / (path.stem + '_' + datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f') + '.wblot')
        backup.write_bytes(path.read_bytes())
        project.project_revision = existing.project_revision
        project.history = existing.history.copy()
        project.refresh_backup = backup.relative_to(root).as_posix()
    saved = _save(project_dir, project, project.project_revision,
                  'refresh_project' if existing else 'import_project', str(backup) if backup else '')
    P.output_dir(path)
    return saved


def confirm_identity_alignment(project_dir, image_set_id, reference_frame_id, evidence, expected_revision):
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("確認した根拠をevidenceに指定してください")
    from wb.rendering import frame_for_capture

    project = _load(project_dir)
    image_set = _get(project.image_sets, "image_set_id", image_set_id)
    frames = [frame_for_capture(project, cid) for cid in image_set.capture_ids]
    if reference_frame_id not in {f.frame_id for f in frames}:
        raise ValueError("基準画像は指定した撮影系列から選んでください")
    for f in frames:
        tid = P.stable_id("TR", "exposure_to_reference", f.frame_id, reference_frame_id)
        previous = next(
            (
                t
                for t in project.transforms
                if t.kind == "exposure_to_reference"
                and t.from_frame_id == f.frame_id
                and t.to_frame_id == reference_frame_id
            ),
            None,
        )
        if previous:
            project.transforms.remove(previous)
        record = Affine.identity(f.frame_id, reference_frame_id).to_json(
            tid, "exposure_to_reference", "confirmed", previous.revision + 1 if previous else 1, evidence
        )
        project.transforms.append(Transform.model_validate(record))
    image_set.reference_frame_id = reference_frame_id
    image_set.geometry_alignment_status = "confirmed"
    return _save(project_dir, project, expected_revision, "confirm_identity_alignment")


def _apply_region(project, region_id, image_set_id, reference_frame_id, polygon, lane_count, assignment_status):
    """Validate and apply a region edit; returns the new project, nothing is saved."""
    region = _get(project.membrane_regions, "region_id", region_id)
    if image_set_id:
        image_set = _get(project.image_sets, "image_set_id", image_set_id)
        if reference_frame_id:
            from wb.rendering import frame_for_capture

            frames = {frame_for_capture(project, c).frame_id for c in image_set.capture_ids}
            if reference_frame_id not in frames and not any(
                t.kind == "exposure_to_reference" and t.from_frame_id in frames and t.to_frame_id == reference_frame_id
                for t in project.transforms
            ):
                raise ValueError("指定した撮影系列から基準画像への位置合わせが必要です")
    if polygon is not None:
        frame = _get(project.frames, "frame_id", reference_frame_id)
        require_within_frame(polygon, frame.width, frame.height)
        region.geometry = FramePolygon(frame_id=reference_frame_id, polygon=polygon)
    else:
        region.geometry = None
    region.image_set_id = image_set_id
    region.reference_frame_id = reference_frame_id
    region.lane_count = lane_count
    region.region_assignment_status = assignment_status
    return type(project).model_validate(project.model_dump())


def _apply_display_geometry(
    project, panel_id, reference_frame_id, rotation_deg, rotation_center, crop_rect, target_width_px, lane_layout
):
    """Validate and apply a display-geometry edit; returns the new project, nothing is saved."""
    panel = _get(project.panels, "panel_id", panel_id)
    frame = _get(project.frames, "frame_id", reference_frame_id)
    region = _get(project.membrane_regions, "region_id", panel.region_id)
    if region.reference_frame_id and region.reference_frame_id != reference_frame_id:
        raise ValueError("表示範囲とメンブレンの基準画像が異なります")
    rect = Rect(**(crop_rect.model_dump() if hasattr(crop_rect, "model_dump") else crop_rect))
    rectified = "RECT_" + panel_id
    transform = Affine.rotation_about(rotation_center, rotation_deg, reference_frame_id, rectified)
    require_within_frame(transform.inverse().apply(rect.corners()), frame.width, frame.height)
    tid = "TR_D_" + panel_id
    prior = next((t for t in project.transforms if t.transform_id == tid), None)
    if prior:
        project.transforms.remove(prior)
    project.transforms.append(
        Transform.model_validate(
            transform.to_json(
                tid,
                "reference_to_rectified",
                "confirmed",
                prior.revision + 1 if prior else 1,
                {"center": list(rotation_center)},
            )
        )
    )
    dg = DisplayGeometry(
        reference_frame_id=reference_frame_id,
        rectified_frame_id=rectified,
        reference_to_rectified_transform_id=tid,
        crop_rect=rect.__dict__,
        target_width_px=target_width_px,
        lane_layout=lane_layout or {},
    )
    project = P.set_display_geometry(project, panel_id, dg)
    return _carry_marker_x(project, panel_id, panel.display_geometry)


def _carry_marker_x(project, panel_id, previous):
    """Keep a hand-set marker x range across crop edits while it still fits the colorimetric image."""
    from wb.rendering import marker_rect, marker_rect_fits

    old = previous.marker_x if previous else None
    if old is None:
        return project
    panel = _get(project.panels, "panel_id", panel_id)
    panel.display_geometry.marker_x = old.model_copy()
    rect = marker_rect(project, panel)
    if rect is None or not marker_rect_fits(project, panel, rect):
        panel.display_geometry.marker_x = None
    return project


def set_marker_x(project_dir, panel_id, x0, x1, expected_revision):
    """Trim or move a Selection's marker horizontally; ``x0=x1=None`` returns to the automatic lane.

    x is in the Selection's rectified frame, like crop_rect. The marker's
    vertical range is always the band crop's, so it is changed with the crop.
    """
    from wb.model import MarkerXRange
    from wb.rendering import marker_parts, marker_rect, marker_rect_fits

    project = _load(project_dir)
    panel = _get(project.panels, "panel_id", panel_id)
    dg = panel.display_geometry
    if dg is None or dg.crop_rect is None:
        raise ValueError("先に段の範囲を設定してください")
    if marker_parts(project, panel) is None:
        raise ValueError("この段にはColorimetricマーカーの位置合わせがありません")
    if x0 is None and x1 is None:
        dg.marker_x = None
        return _save(project_dir, project, expected_revision, "set_marker_x", f"{panel_id}: 自動")
    if x0 is None or x1 is None or x1 - x0 < 2:
        raise ValueError("マーカーの左右を指定してください（幅2以上）")
    dg.marker_x = MarkerXRange(x0=x0, x1=x1)
    if not marker_rect_fits(project, panel, marker_rect(project, panel)):
        raise ValueError("マーカーの範囲がColorimetric画像からはみ出しています")
    return _save(project_dir, project, expected_revision, "set_marker_x", panel_id)


def update_region(
    project_dir, region_id, image_set_id, reference_frame_id, polygon, lane_count, assignment_status, expected_revision
):
    project = _apply_region(
        _load(project_dir), region_id, image_set_id, reference_frame_id, polygon, lane_count, assignment_status
    )
    return _save(project_dir, project, expected_revision, "update_region")


def update_display_geometry(
    project_dir,
    panel_id,
    reference_frame_id,
    rotation_deg,
    rotation_center,
    crop_rect,
    target_width_px,
    lane_layout,
    expected_revision,
):
    project = _apply_display_geometry(
        _load(project_dir),
        panel_id,
        reference_frame_id,
        rotation_deg,
        rotation_center,
        crop_rect,
        target_width_px,
        lane_layout,
    )
    return _save(project_dir, project, expected_revision, "update_display_geometry")


def save_panel_edit(
    project_dir,
    panel_id,
    expected_revision,
    region=None,
    display_geometry=None,
    annotations=None,
    selected_exposure_id=None,
):
    """Apply one panel edit as a single project revision.

    ``region`` takes the keyword arguments of update_region (without region_id),
    ``display_geometry`` those of update_display_geometry (without panel_id),
    ``annotations`` is a list of annotation dicts for this panel. Every part is
    validated and applied to an in-memory project first; the file is written
    once, at the end, so a failure in any part leaves project.json untouched.
    """
    parts = {
        "region": region,
        "display_geometry": display_geometry,
        "annotations": annotations,
        "selected_exposure_id": selected_exposure_id,
    }
    applied = [name for name, value in parts.items() if value is not None]
    if not applied:
        raise ValueError("保存する変更がありません")
    project = _load(project_dir)
    panel = _get(project.panels, "panel_id", panel_id)
    if region is not None:
        project = _apply_region(project, panel.region_id, **region)
    if display_geometry is not None:
        project = _apply_display_geometry(project, panel_id, **display_geometry)
    for annotation in annotations or []:
        if annotation.get("panel_id", panel_id) != panel_id:
            raise ValueError("注釈が別の段に属しています")
        project = P.add_annotation(project, {**annotation, "panel_id": panel_id})
    if selected_exposure_id is not None:
        project = P.set_selected_exposure(project, panel_id, selected_exposure_id)
    return _save(project_dir, project, expected_revision, "save_panel_edit", f"{panel_id}: " + ", ".join(applied))


def add_annotation(project_dir, annotation, expected_revision):
    return _save(project_dir, P.add_annotation(_load(project_dir), annotation), expected_revision, "add_annotation")


def confirm_annotations(project_dir, annotation_ids, expected_revision):
    project = _load(project_dir)
    for aid in annotation_ids:
        project = P.confirm_annotation(project, aid)
    return _save(project_dir, project, expected_revision, "confirm_annotations")


def set_selected_exposure(project_dir, panel_id, capture_id, expected_revision):
    return _save(
        project_dir,
        P.set_selected_exposure(_load(project_dir), panel_id, capture_id),
        expected_revision,
        "set_selected_exposure",
    )


def propose_annotations(project_dir, *args, **kwargs):
    _load(project_dir)
    return {"proposals": [], "note": "Annotation proposals are planned for phase 4."}


def render_project(project_dir):
    from wb import rendering

    root = P.locate(project_dir).parent
    project = _load(project_dir)
    result = {"panels": [], "overviews": [], "errors": []}
    for frame in project.frames:
        source = _get(project.sources, "source_asset_id", frame.source_asset_id)
        if source.representation_kind != "presentation_export_candidate":
            continue
        image, _ = rendering.render_overview(project, root, frame.frame_id)
        result["overviews"].append(str(image.cache_path))
    for panel in project.panels:
        if not panel.display_geometry or not panel.display_geometry.crop_rect:
            continue
        for capture_id in panel_capture_ids(project, panel):
            try:
                image = rendering.render_panel(project, root, panel.panel_id, capture_id)
                result["panels"].append(str(image.cache_path))
            except rendering.MissingTransform:
                result["errors"].append(
                    {"panel_id": panel.panel_id, "capture_id": capture_id, "reason": "位置合わせが未設定です"}
                )
    return result


def panel_capture_ids(project, panel):
    region = _get(project.membrane_regions, "region_id", panel.region_id)
    sid = panel.image_set_id or region.image_set_id
    image_set = next((s for s in project.image_sets if s.image_set_id == sid), None)
    return image_set.capture_ids if image_set else []


def validate(project_dir):
    project = _load(project_dir)
    issues = P.verify_sources(project, P.locate(project_dir).parent)
    flagged = P.flag_dependents(project, issues)
    return {
        "source_issues": [asdict(i) for i in issues],
        "unresolved_items": project.unresolved_items,
        "output_readiness": output_readiness(flagged),
    }


def import_metadata(project_dir, metadata_xlsx, expected_revision):
    from wb.model import Sample, SampleGroup

    project = _load(project_dir)
    rows = read_metadata_xlsx(metadata_xlsx).panels
    for row in rows:
        label = row.get("membrane_label")
        if not label:
            raise ValueError("メタデータにはmembrane_labelが必要です")
        project = P.add_region(project, str(label), row.get("label"))
        region = _get(project.membrane_regions, "user_label", str(label))
        panel = _get(project.panels, "region_id", region.region_id)
        target = row.get("label")
        if target and region.target_assignment_status == "confirmed" and region.target_label != target:
            raise ValueError(f"確定済みの抗体ラベルと矛盾します {label}: {region.target_label} / {target}")
        if target:
            region.target_label = target
            panel.label = target
        if row.get("image_set_id"):
            _get(project.image_sets, "image_set_id", row["image_set_id"])
            region.image_set_id = row["image_set_id"]
        if row.get("lane_count") is not None:
            region.lane_count = row["lane_count"]
        if row.get("sample_group_id"):
            panel.sample_group_id = str(row["sample_group_id"])
        panel.display_order = row["display_order"]
    for panel in project.panels:
        if not panel.sample_group_id:
            continue
        region = _get(project.membrane_regions, "region_id", panel.region_id)
        group = next((g for g in project.sample_groups if g.sample_group_id == panel.sample_group_id), None)
        if group is None:
            group = SampleGroup(sample_group_id=panel.sample_group_id)
            project.sample_groups.append(group)
        count = region.lane_count
        if count is not None and group.lane_count is not None and count != group.lane_count:
            raise ValueError(f"同じ試料グループのレーン数が異なります {group.sample_group_id}")
        if count is not None and group.lane_count is None:
            group.lane_count = count
            group.samples = [Sample() for _ in range(count)]
    project = type(project).model_validate(project.model_dump())
    return _save(project_dir, project, expected_revision, "import_metadata")


def add_region(project_dir, user_label, target_label, expected_revision):
    if not isinstance(user_label, str) or not user_label.strip():
        raise ValueError("user_labelを指定してください")
    return _save(
        project_dir,
        P.add_region(_load(project_dir), user_label.strip(), target_label),
        expected_revision,
        "add_region",
    )


def set_control_for(project_dir, panel_id, control_for_panel_ids, expected_revision):
    project = _load(project_dir)
    panel = _get(project.panels, "panel_id", panel_id)
    if len(set(control_for_panel_ids)) != len(control_for_panel_ids) or panel_id in control_for_panel_ids:
        raise ValueError("対照先に同じ段や対照自身を指定できません")
    for pid in control_for_panel_ids:
        _get(project.panels, "panel_id", pid)
    panel.control_for_panel_ids = control_for_panel_ids
    return _save(project_dir, project, expected_revision, "set_control_for")


def set_sample_names(project_dir, sample_group_id, samples, expected_revision):
    from wb.model import Sample

    project = _load(project_dir)
    group = _get(project.sample_groups, "sample_group_id", sample_group_id)
    if group.lane_count is None or len(samples) != group.lane_count:
        raise ValueError("試料数をレーン数に合わせてください")
    group.samples = [Sample.model_validate(s if isinstance(s, dict) else {"name": s}) for s in samples]
    return _save(project_dir, project, expected_revision, "set_sample_names")


def add_marker_registration(
    project_dir, membrane_region_id, marker_frame_id, matrix, marker_roi_polygon, status, evidence, expected_revision
):
    from wb.model import MarkerRegistration

    project = _load(project_dir)
    region = _get(project.membrane_regions, "region_id", membrane_region_id)
    marker = _get(project.frames, "frame_id", marker_frame_id)
    if not region.reference_frame_id:
        raise ValueError("メンブレンの基準画像を指定してください")
    if status not in ("candidate", "confirmed"):
        raise ValueError("statusはcandidateかconfirmedを指定してください")
    if status == "confirmed" and not evidence:
        raise ValueError("確定には確認した根拠が必要です")
    require_within_frame(marker_roi_polygon, marker.width, marker.height)
    roi = FramePolygon(frame_id=marker_frame_id, polygon=marker_roi_polygon)
    affine = Affine(marker_frame_id, region.reference_frame_id, matrix)
    affine.inverse()
    rid = P.stable_id("MARKER", membrane_region_id)
    tid = P.stable_id("TR", "marker_to_reference", membrane_region_id)
    old = next((t for t in project.transforms if t.transform_id == tid), None)
    project.transforms = [t for t in project.transforms if t.transform_id != tid]
    project.transforms.append(
        Transform.model_validate(
            affine.to_json(tid, "marker_to_reference", status, old.revision + 1 if old else 1, evidence)
        )
    )
    project.marker_registrations = [r for r in project.marker_registrations if r.marker_registration_id != rid]
    project.marker_registrations.append(
        MarkerRegistration(
            marker_registration_id=rid,
            membrane_region_id=membrane_region_id,
            transform_id=tid,
            marker_roi=roi,
            status=status,
        )
    )
    for panel in project.panels:
        if panel.region_id == membrane_region_id:
            panel.marker_registration_id = rid
    return _save(project_dir, project, expected_revision, "add_marker_registration")


def fit_marker_landmarks(marker_points, reference_points):
    import cv2
    import numpy as np

    src = np.asarray(marker_points, dtype=np.float64)
    dst = np.asarray(reference_points, dtype=np.float64)
    if (
        src.ndim != 2
        or src.shape[1:] != (2,)
        or src.shape != dst.shape
        or len(src) < 2
        or not np.isfinite(src).all()
        or not np.isfinite(dst).all()
    ):
        raise ValueError("有限の座標で対応点を2組以上指定してください")
    # OpenCV estimates in its own pixel-centre convention; conjugate back to edge coordinates.
    fitted, inliers = cv2.estimateAffinePartial2D(
        (src - 0.5).astype(np.float32), (dst - 0.5).astype(np.float32), method=cv2.LMEDS
    )
    if fitted is None:
        raise ValueError("この対応点からは相似変換を求められません")
    mask = inliers.ravel().astype(bool)
    a = np.zeros((2 * mask.sum(), 4))
    xy = src[mask]
    a[0::2] = np.column_stack((xy[:, 0], -xy[:, 1], np.ones(len(xy)), np.zeros(len(xy))))
    a[1::2] = np.column_stack((xy[:, 1], xy[:, 0], np.zeros(len(xy)), np.ones(len(xy))))
    coeff, _, rank, _ = np.linalg.lstsq(a, dst[mask].ravel(), rcond=None)
    if rank < 4:
        raise ValueError("この対応点からは相似変換を求められません")
    c, s, tx, ty = coeff
    matrix = [[c, -s, tx], [s, c, ty], [0.0, 0.0, 1.0]]
    residuals = np.linalg.norm(Affine("marker", "reference", matrix).apply(src) - dst, axis=1)
    return {"matrix": matrix, "residuals": residuals.tolist(), "inliers": mask.tolist()}


def propose(project_dir):
    """Compute layout proposals without modifying the project."""
    from wb.propose import propose_layout

    return propose_layout(_load(project_dir), P.locate(project_dir).parent)


def _apply_proposal_transform(project, raw, reference, replace_existing):
    kind, source, destination = raw["kind"], raw["from_frame_id"], raw["to_frame_id"]
    if kind not in ("marker_to_reference", "exposure_to_reference") or destination != reference:
        raise ValueError("提案の位置合わせが不正です")
    _get(project.frames, "frame_id", source)
    affine = Affine(source, destination, raw["matrix"])
    affine.inverse()
    old = next(
        (
            t
            for t in project.transforms
            if t.kind == kind and t.from_frame_id == source and t.to_frame_id == destination
        ),
        None,
    )
    if old and old.status == "confirmed" and not replace_existing:
        if old.matrix != affine.matrix.tolist():
            raise ValueError("提案が確定済みの位置合わせを上書きします")
        return old.transform_id
    tid = P.stable_id("TR", kind, source, destination)
    if old:
        project.transforms.remove(old)
    project.transforms.append(
        Transform.model_validate(
            affine.to_json(
                tid,
                kind,
                "candidate",
                old.revision + 1 if old else 1,
                {"method": raw.get("method"), "score": raw.get("score")},
            )
        )
    )
    return tid


def _proposal_region(project, group, membrane, suffix, replace_existing):
    from wb.model import MembraneRegion

    rid = "REGION_" + suffix
    old_region = next((r for r in project.membrane_regions if r.region_id == rid), None)
    old_panels = [p for p in project.panels if p.region_id == rid]
    region_confirmed = old_region and old_region.region_assignment_status == "confirmed"
    if not replace_existing and (region_confirmed or any(p.review_status == "confirmed" for p in old_panels)):
        raise ValueError(f"提案が確定済みのメンブレンか段を上書きします {rid}")
    reference = _get(project.frames, "frame_id", group["reference_frame_id"])
    polygon = membrane["polygon_reference_frame"]
    require_within_frame(polygon, reference.width, reference.height)
    if group["marker_frame_id"] is not None:
        marker = _get(project.frames, "frame_id", group["marker_frame_id"])
        require_within_frame(membrane["polygon_marker_frame"], marker.width, marker.height)
    if not group["image_set_ids"]:
        raise ValueError("提案グループには撮影系列が必要です")
    region = MembraneRegion(
        region_id=rid,
        user_label=membrane["label"],
        reference_frame_id=reference.frame_id,
        image_set_id=group["image_set_ids"][0],
        geometry=FramePolygon(frame_id=reference.frame_id, polygon=polygon),
        region_assignment_status="proposed",
        created_by="algorithm",
        band_row_candidates=membrane["row_candidates"],
    )
    project.membrane_regions = [r for r in project.membrane_regions if r.region_id != rid] + [region]
    project.panels = [p for p in project.panels if p.region_id != rid]
    old_panel_ids = {p.panel_id for p in old_panels}
    project.annotations = [a for a in project.annotations if a.panel_id not in old_panel_ids]
    project.marker_registrations = [m for m in project.marker_registrations if m.membrane_region_id != rid]
    return region


def _proposal_rotation(project, membrane, suffix, reference):
    rotation = membrane["rotation"]
    d = Affine.rotation_about(rotation["center"], rotation["degrees"], reference, "RECT_" + suffix)
    tid = "TR_D_" + suffix
    prior = next((t for t in project.transforms if t.transform_id == tid), None)
    project.transforms = [t for t in project.transforms if t.transform_id != tid]
    project.transforms.append(
        Transform.model_validate(
            d.to_json(
                tid,
                "reference_to_rectified",
                "candidate",
                prior.revision + 1 if prior else 1,
                {"method": rotation.get("method"), "evidence": rotation.get("evidence"), "center": rotation["center"]},
            )
        )
    )
    return d, tid


def _proposal_marker_registration(project, region, membrane, marker_frame_id, marker_tid, d):
    from wb.model import MarkerRegistration

    marker_roi = None
    lanes = membrane["marker_lanes"]
    if lanes is not None:
        if not marker_tid:
            raise ValueError("マーカーレーンには位置合わせが必要です")
        marker = _get(project.frames, "frame_id", marker_frame_id)
        transform = _get(project.transforms, "transform_id", marker_tid)
        marker_to_rect = d @ Affine.from_json(transform.model_dump())
        rect_poly = marker_to_rect.apply(membrane["polygon_marker_frame"])
        y0, y1 = min(p[1] for p in rect_poly), max(p[1] for p in rect_poly)
        rect = Rect(lanes["x0_rectified"], y0, lanes["x1_rectified"], y1)
        points = marker_to_rect.inverse().apply(rect.corners()).tolist()
        require_within_frame(points, marker.width, marker.height)
        marker_roi = FramePolygon(frame_id=marker_frame_id, polygon=points)
    registration = MarkerRegistration(
        marker_registration_id=P.stable_id("MARKER", region.region_id),
        membrane_region_id=region.region_id,
        transform_id=marker_tid,
        marker_roi=marker_roi,
        status="candidate" if marker_roi else "pending",
    )
    project.marker_registrations.append(registration)
    return registration.marker_registration_id


def _proposal_panel(project, region, membrane, sets, d, dtid, registration_id):
    from wb.model import BandRowCandidate, Panel
    from wb.rendering import frame_for_capture

    row = BandRowCandidate.model_validate(membrane["row"])
    chosen = next((c for c in region.band_row_candidates if c.candidate_id == row.candidate_id), None)
    if chosen != row:
        raise ValueError("採用する段がrow_candidatesに含まれていません")
    reference = _get(project.frames, "frame_id", region.reference_frame_id)
    for candidate in region.band_row_candidates:
        capture = candidate.selected_exposure_id
        _get(project.captures, "capture_id", capture)
        if not any(capture in image_set.capture_ids for image_set in sets):
            raise ValueError(f"capture {capture} is not in proposal image sets")
        frame_for_capture(project, capture)
        rect = Rect(**candidate.crop_rect_rectified.model_dump())
        require_within_frame(d.inverse().apply(rect.corners()), reference.width, reference.height)
    image_set = next(s for s in sets if row.selected_exposure_id in s.capture_ids)
    dg = DisplayGeometry(
        reference_frame_id=reference.frame_id,
        rectified_frame_id=d.to_frame_id,
        reference_to_rectified_transform_id=dtid,
        crop_rect=row.crop_rect_rectified,
        target_width_px=600,
        lane_layout={
            "lane_count": None,
            "centers_x": membrane["lane_candidates"]["centers_x_rectified"],
            "status": "proposed",
        },
    )
    project.panels.append(
        Panel(
            panel_id=f"PANEL_{region.region_id}_{membrane['panel_label']}",
            region_id=region.region_id,
            label=membrane["panel_label"],
            image_set_id=image_set.image_set_id,
            display_order=len(project.panels) + 1,
            selected_exposure_id=row.selected_exposure_id,
            exposure_reason=row.exposure_reason,
            selected_band_row_candidate_id=row.candidate_id,
            display_geometry=dg,
            marker_registration_id=registration_id,
            review_status="proposed",
        )
    )


def _apply_proposal_group(project, group, group_prefix, replace_existing):
    reference = group["reference_frame_id"]
    _get(project.frames, "frame_id", reference)
    if group["marker_frame_id"] is not None:
        _get(project.frames, "frame_id", group["marker_frame_id"])
    sets = [_get(project.image_sets, "image_set_id", sid) for sid in group["image_set_ids"]]
    transform_ids = {}
    for raw in group["transforms"]:
        tid = _apply_proposal_transform(project, raw, reference, replace_existing)
        transform_ids[(raw["kind"], raw["from_frame_id"])] = tid
    marker_tid = transform_ids.get(("marker_to_reference", group["marker_frame_id"]))
    for membrane in group["membranes"]:
        suffix = group_prefix + membrane["label"]
        region = _proposal_region(project, group, membrane, suffix, replace_existing)
        d, dtid = _proposal_rotation(project, membrane, suffix, reference)
        registration_id = None if group["marker_frame_id"] is None else _proposal_marker_registration(
            project, region, membrane, group["marker_frame_id"], marker_tid, d
        )
        _proposal_panel(project, region, membrane, sets, d, dtid, registration_id)


def apply_proposal(project_dir, proposal, expected_revision, replace_existing=False):
    """Apply a format-3 proposal atomically, retaining human decisions by default."""
    project = _load(project_dir)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    if not isinstance(proposal, dict) or proposal.get("proposal_format") != 3:
        raise ValueError("proposal_formatは3である必要があります")
    digest = hashlib.sha256(json.dumps(proposal, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if getattr(project, "applied_proposal_sha256", None) == digest:
        return project
    groups = proposal["groups"]
    for index, group in enumerate(groups, 1):
        prefix = f"G{index}_" if len(groups) > 1 else ""
        _apply_proposal_group(project, group, prefix, replace_existing)
    project.proposal_diagnostics = {
        k: proposal.get(k, []) for k in ("excluded_image_sets", "unmatched_marker_frames", "warnings")
    }
    project.proposal_diagnostics["group_warnings"] = [w for g in groups for w in g.get("warnings", [])]
    project.applied_proposal_sha256 = digest
    for panel in project.panels:
        if panel.included:
            _ensure_colorimetric_marker(project, project_dir, panel, confirm=False)
    project = type(project).model_validate(project.model_dump())
    return _save(project_dir, project, expected_revision, "apply_proposal")


def select_band_row(project_dir, panel_id, candidate_id, expected_revision):
    """Select a membrane's band row while retaining the edited rotation and width."""
    project = _load(project_dir)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    project = P.select_band_row(project, panel_id, candidate_id)
    panel = _get(project.panels, "panel_id", panel_id)
    dg = panel.display_geometry
    transform = _get(project.transforms, "transform_id", dg.reference_to_rectified_transform_id)
    frame = _get(project.frames, "frame_id", dg.reference_frame_id)
    points = Affine.from_json(transform.model_dump()).inverse().apply(Rect(**dg.crop_rect.model_dump()).corners())
    require_within_frame(points, frame.width, frame.height)
    from wb.rendering import exposure_to_reference

    exposure_to_reference(project, panel.selected_exposure_id, dg.reference_frame_id)
    return _save(
        project_dir, project, expected_revision, "select_band_row", f"human selected {candidate_id} for {panel_id}"
    )


def confirm_transform(project_dir, transform_id, evidence, expected_revision):
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("確認した根拠をevidenceに指定してください")
    project = _load(project_dir)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    transform = _get(project.transforms, "transform_id", transform_id)
    if transform.status == "confirmed":
        return project
    transform.status = "confirmed"
    transform.evidence = evidence.strip()
    transform.revision += 1
    for registration in project.marker_registrations:
        if registration.transform_id == transform_id and registration.marker_roi:
            registration.status = "confirmed"
    return _save(project_dir, project, expected_revision, "confirm_transform")


def rename_panel(project_dir, panel_id, label, expected_revision):
    if not isinstance(label, str) or not label.strip():
        raise ValueError("段のラベルを指定してください")
    project = _load(project_dir)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    panel = _get(project.panels, "panel_id", panel_id)
    if panel.label == label.strip():
        return project
    panel.label = label.strip()
    return _save(project_dir, project, expected_revision, "rename_panel")


def confirm_panel(project_dir, panel_id, expected_revision, evidence=None):
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("段を確認した根拠をevidenceに指定してください")
    project = _load(project_dir)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    panel = _get(project.panels, "panel_id", panel_id)
    if panel.review_status == "confirmed":
        return project
    panel.review_status = "confirmed"
    return _save(project_dir, project, expected_revision, "confirm_panel", evidence.strip())


def resolve_panel(project, label):
    matches = [p for p in project.panels if p.panel_id == label]
    if not matches:
        matches = [p for p in project.panels if p.label == label]
    if not matches:
        # A membrane name (M1) also works while that membrane has a single Selection.
        regions = {r.region_id for r in project.membrane_regions if r.user_label == label}
        matches = [p for p in project.panels if p.region_id in regions]
    if len(matches) != 1:
        raise ValueError(f"段を一意に指定できません。panel_idを指定してください: {label}")
    return matches[0]


def panel_summaries(project):
    """Use the same panel entries in status and CLI mutation receipts."""
    from wb.rendering import marker_rect

    panels = []
    for panel in sorted(project.panels, key=lambda p: p.display_order or 0):
        region = _get(project.membrane_regions, "region_id", panel.region_id)
        capture = next((c for c in project.captures if c.capture_id == panel.selected_exposure_id), None)
        panels.append(
            {
                "panel_id": panel.panel_id,
                "label": panel.label,
                "included": panel.included,
                "display_order": panel.display_order,
                "lane_count": region.lane_count,
                "membrane_label": region.user_label,
                "region_id": region.region_id,
                "review_status": panel.review_status,
                "region_assignment_status": region.region_assignment_status,
                "selected_band_row_candidate_id": panel.selected_band_row_candidate_id,
                "selected_exposure_id": panel.selected_exposure_id,
                "exposure_s": capture.exposure_s if capture else None,
                "exposure_reason": panel.exposure_reason,
                "geometry_status": "set" if panel.display_geometry and panel.display_geometry.crop_rect else "unset",
                "crop_rect": _rect_summary(panel.display_geometry.crop_rect if panel.display_geometry else None),
                "marker_rect": _rect_summary(marker_rect(project, panel)),
                "marker_source": "manual" if panel.display_geometry and panel.display_geometry.marker_x else "auto",
            }
        )
    return panels


def _rect_summary(rect):
    if rect is None:
        return None
    return {k: round(float(getattr(rect, k)), 1) for k in ("x0", "y0", "x1", "y1")}


def status(project_dir):
    project = _load(project_dir)
    checked = P.flag_dependents(project, P.verify_sources(project, P.locate(project_dir).parent))
    readiness = output_readiness(checked)
    readiness["blocking_items"] = [
        dict(item, text=REASON_TEXT.get(item["reason"], item["reason"])) for item in readiness["blocking_items"]
    ]
    diagnostics = getattr(project, "proposal_diagnostics", {}) or {}
    additions = unimported_sources(project, P.locate(project_dir).parent)
    source_warnings = ([f'未取り込みの元ファイルが{len(additions)}件あります。'
                        '取り込み直すには wbm init <画像フォルダ> --refresh を実行してください。']
                       if additions else [])
    return {
        "project_revision": project.project_revision,
        "panels": panel_summaries(project),
        "unresolved_items": project.unresolved_items,
        "excluded_image_sets": diagnostics.get("excluded_image_sets", []),
        "warnings": source_warnings + diagnostics.get("warnings", []) + diagnostics.get("group_warnings", []),
        "readiness": readiness,
    }


def rows(project_dir, panel=None):
    project = _load(project_dir)
    panels = [resolve_panel(project, panel)] if panel else project.panels
    return [
        {
            "panel_id": p.panel_id,
            "label": p.label,
            "selected_band_row_candidate_id": p.selected_band_row_candidate_id,
            "candidates": [
                c.model_dump(mode="json")
                for c in _get(project.membrane_regions, "region_id", p.region_id).band_row_candidates
            ],
        }
        for p in panels
    ]


def adjust(
    project_dir,
    panel_id,
    expected_revision,
    rotation=None,
    crop_top=None,
    crop_bottom=None,
    width=None,
    crop_left=None,
    crop_right=None,
):
    import math

    project = _load(project_dir)
    panel = _get(project.panels, "panel_id", panel_id)
    dg = panel.display_geometry
    if not dg or not dg.crop_rect:
        raise ValueError("先に段の候補を反映してください")
    if all(value is None for value in (rotation, crop_top, crop_bottom, width, crop_left, crop_right)):
        raise ValueError("変更する角度、範囲、幅を指定してください")
    transform = _get(project.transforms, "transform_id", dg.reference_to_rectified_transform_id)
    current_angle = math.degrees(math.atan2(transform.matrix[1][0], transform.matrix[0][0]))
    evidence = transform.evidence if isinstance(transform.evidence, dict) else {}
    center = evidence.get("center") or evidence.get("rotation_center")
    crop = dg.crop_rect.model_dump()
    if center is None:
        # Recover the original pivot from t = (I - R)c, including imported proposals.
        import numpy as np

        matrix = np.asarray(transform.matrix)
        center = (
            np.linalg.lstsq(np.eye(2) - matrix[:2, :2], matrix[:2, 2], rcond=None)[0].tolist()
            if abs(current_angle) > 1e-8
            else [0.0, 0.0]
        )
    if crop_left is not None:
        crop["x0"] = crop_left
    if crop_right is not None:
        crop["x1"] = crop_right
    if crop_top is not None:
        crop["y0"] = crop_top
    if crop_bottom is not None:
        crop["y1"] = crop_bottom
    return update_display_geometry(
        project_dir,
        panel_id,
        dg.reference_frame_id,
        current_angle if rotation is None else rotation,
        center,
        crop,
        dg.target_width_px if width is None else width,
        dg.lane_layout.model_dump(),
        expected_revision,
    )


def confirm_alignment(project_dir, image_set_id, evidence, expected_revision):
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("位置合わせを確認した根拠が必要です")
    from wb.rendering import frame_for_capture

    project = _load(project_dir)
    image_set = _get(project.image_sets, "image_set_id", image_set_id)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    frames = {frame_for_capture(project, cid).frame_id for cid in image_set.capture_ids}
    transforms = [t for t in project.transforms if t.kind == "exposure_to_reference" and t.from_frame_id in frames]
    if not transforms:
        raise ValueError("この系列には位置合わせ候補がありません")
    for transform in transforms:
        transform.status = "confirmed"
        transform.evidence = evidence.strip()
        transform.revision += 1
    image_set.geometry_alignment_status = "confirmed"
    return _save(project_dir, project, expected_revision, "confirm_alignment", evidence.strip())


def export_project(project_dir, kind, expected_revision=None, merge=True):
    if kind not in ("pptx", "png"):
        raise ValueError("書き出し形式はpptxかpngを指定してください")
    root = P.locate(project_dir).parent
    project = _load(project_dir)
    if expected_revision is not None and expected_revision != project.project_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    validation = validate(project_dir)
    if validation["source_issues"]:
        raise ValueError("元画像が変更されたか見つかりません。書き出し前に確認してください")
    from wb import export

    exports = P.output_dir(root) / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    if kind == "png":
        path = exports / f"{root.name}.png"
        export.export_png(project, root, path)
        from wb.layout import compute_layout

        pages = len(compute_layout(project)["pages"])
        names = [path.name] + [f"{path.stem}-p{i}.png" for i in range(2, pages + 1)]
        return {"path": str(path), "kind": kind, "files": names}
    # PPTX names carry a local-time timestamp and never overwrite an earlier file.
    stamp = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f%z')
    base = f"{root.name}_{stamp}"
    for index in itertools.count():
        candidate = exports / f"{base}{f'-{index}' if index else ''}.pptx"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        break
    try:
        export.export_pptx(project, root, candidate, merge=merge)
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise
    return {"path": str(candidate), "kind": kind, "files": [candidate.name]}


def export_file(project_dir, name):
    """An exported file by bare name, only from this project's exports folder."""
    if not isinstance(name, str) or not name or name != Path(name).name or name.startswith("."):
        raise ValueError("ファイル名が不正です")
    if Path(name).suffix.lower() not in (".png", ".pptx"):
        raise ValueError("ダウンロードできるのはPNGかPPTXです")
    path = P.output_dir(P.locate(project_dir).parent) / "exports" / name
    if not path.is_file():
        raise FileNotFoundError(f"書き出したファイルが見つかりません: {name}")
    return path


def install_skill(claude=False, codex=False, agents=False):
    """Copy the wblot skill into ~/.claude/skills, ~/.codex/skills and ~/.agents/skills (all by default)."""
    import shutil

    canonical = Path(__file__).resolve().parents[2] / "skills/wblot/SKILL.md"
    source = canonical if canonical.is_file() else Path(__file__).parent / "skills/wblot/SKILL.md"
    if not source.is_file():
        raise ValueError("配布されたSKILL.mdが見つかりません")
    destinations = []
    for name, selected in ((".claude", claude), (".codex", codex), (".agents", agents)):
        if selected or not (claude or codex or agents):
            destination = Path.home() / name / "skills/wblot/SKILL.md"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or destination.read_bytes() != source.read_bytes():
                shutil.copyfile(source, destination)
            destinations.append(str(destination))
    return {"paths": destinations}


def init_project(path, refresh=False):
    import_project(path, refresh=refresh)
    location = P.locate(path)
    return {"path": str(location), "project_revision": _load(location).project_revision}


def prepare_project(path, propose_new=False):
    """Return ``(project file, created)`` for a ZIP, image folder or .wblot, importing it if needed.

    ``propose_new`` applies the initial proposal to a project created just now,
    so a page opened from the top page does not start empty. A failed proposal
    leaves the empty project; membranes can still be added by hand.
    """
    target = Path(path).expanduser().absolute()
    if target.suffix == ".wblot":
        return P.locate(target), False
    try:
        P.locate(target)
        existed = True
    except P.ProjectError:
        existed = False
    location = Path(init_project(target)["path"])
    if propose_new and not existed and not _load(location).membrane_regions:
        try:
            proposal = propose(location)
            if proposal.get("groups"):
                apply_proposal(location, proposal, _load(location).project_revision)
        except Exception:  # noqa: BLE001 - an empty project is still usable
            pass
    return location, not existed


def open_project(path=None, no_browser=False):
    from wb.server import open_project as start_server

    location = P.locate() if path is None else prepare_project(path)[0]
    return start_server(location, no_browser)


def stop_project(path=None):
    """Stop the shared server; every project's page closes with it."""
    from wb.server import stop_server

    return stop_server()


def recent_projects():
    from wb.hub import recent

    return {"projects": recent()}


def layout(project_dir):
    from wb.layout import compute_layout

    return compute_layout(_load(project_dir))


def set_panel_included(project_dir, panel_id, included, expected_revision):
    project = _load(project_dir)
    panel = _get(project.panels, "panel_id", panel_id)
    panel.included = bool(included)
    _get(project.membrane_regions, "region_id", panel.region_id).suspended_panel_ids = []
    return _save(project_dir, project, expected_revision, "set_panel_included")


def set_membrane_included(project_dir, region_id, included, expected_revision):
    project = _load(project_dir)
    region = _get(project.membrane_regions, "region_id", region_id)
    if not isinstance(included, bool):
        raise ValueError("included は真偽値で指定してください")
    region.included = included
    return _save(project_dir, project, expected_revision, "set_membrane_included")


def delete_panel(project_dir, panel_id, expected_revision):
    project = _load(project_dir)
    panel = _get(project.panels, "panel_id", panel_id)
    project.panels.remove(panel)
    project.annotations = [a for a in project.annotations if a.panel_id != panel_id]
    for item in project.panels:
        item.control_for_panel_ids = [pid for pid in item.control_for_panel_ids if pid != panel_id]
    for rule in project.unassigned_target_rules:
        rule.control_for_panel_ids = [pid for pid in rule.control_for_panel_ids if pid != panel_id]
    for region in project.membrane_regions:
        region.suspended_panel_ids = [pid for pid in region.suspended_panel_ids if pid != panel_id]
    if project.review_state and project.review_state.selected_panel_id == panel_id:
        project.review_state.selected_panel_id = None
    project.review_flags = [f for f in project.review_flags if f.target_id != panel_id]
    for order, item in enumerate(_ordered_panels(project), 1):
        item.display_order = order
    return _save(project_dir, project, expected_revision, "delete_panel", f"Selection {panel.label}")


def _ordered_panels(project):
    return sorted(project.panels, key=lambda p: p.display_order if p.display_order is not None else float("inf"))


def duplicate_panel(project_dir, panel_id, expected_revision):
    project = _load(project_dir)
    source = _get(project.panels, "panel_id", panel_id)
    region = _get(project.membrane_regions, "region_id", source.region_id)
    ids = {p.panel_id for p in project.panels}
    n = 1
    while f"{panel_id}_DUP{n}" in ids or any(t.transform_id == f"TR_D_{panel_id}_DUP{n}" for t in project.transforms):
        n += 1
    new_id = f"{panel_id}_DUP{n}"
    labels = {p.label for p in project.panels}
    label = next((chr(c) for c in range(ord('A'), ord('Z') + 1) if chr(c) not in labels), None)
    if label is None:
        label = f"{source.label}-2"
        suffix = 2
        while label in labels:
            suffix += 1
            label = f"{source.label}-{suffix}"
    panel = source.model_copy(deep=True)
    panel.panel_id, panel.label, panel.included, panel.review_status = new_id, label, True, "proposed"
    panel.band_annotation_ids = []
    panel.background_roi_ids = []
    dg = panel.display_geometry
    if dg and dg.reference_to_rectified_transform_id:
        transform = _get(project.transforms, "transform_id", dg.reference_to_rectified_transform_id).model_copy(deep=True)
        transform.transform_id = "TR_D_" + new_id
        transform.to_frame_id = "RECT_" + new_id
        dg.reference_to_rectified_transform_id = transform.transform_id
        dg.rectified_frame_id = transform.to_frame_id
        project.transforms.append(transform)
    if dg and dg.crop_rect:
        transform = _get(project.transforms, "transform_id", dg.reference_to_rectified_transform_id)
        frame = _get(project.frames, "frame_id", dg.reference_frame_id)
        rect = Rect(**dg.crop_rect.model_dump())
        require_within_frame(Affine.from_json(transform.model_dump()).inverse().apply(rect.corners()), frame.width, frame.height)
    ordered = _ordered_panels(project)
    ordered.insert(ordered.index(source) + 1, panel)
    project.panels.append(panel)
    for order, item in enumerate(ordered, 1):
        item.display_order = order
    return _save(project_dir, project, expected_revision, "duplicate_panel")


def reorder_panels(project_dir, panel_ids, expected_revision):
    if not isinstance(panel_ids, list) or not panel_ids or not all(isinstance(x, str) for x in panel_ids) or len(set(panel_ids)) != len(panel_ids):
        raise ValueError("重複のない段のIDを指定してください")
    project = _load(project_dir)
    ordered = [_get(project.panels, "panel_id", pid) for pid in panel_ids]
    ordered += [p for p in _ordered_panels(project) if p.panel_id not in panel_ids]
    for order, panel in enumerate(ordered, 1):
        panel.display_order = order
        panel.display_order_status = "confirmed"
    return _save(project_dir, project, expected_revision, "reorder_panels")


def set_figure_scale(project_dir, mode, expected_revision):
    if mode != "equal_lane_width":
        raise ValueError("表示縮尺は1レーンの幅をそろえる方式に固定されています")
    project = _load(project_dir)
    project.figure_scale_mode = mode
    return _save(project_dir, project, expected_revision, "set_figure_scale")


def set_lanes(project_dir, lane_count, expected_revision, panel_id=None, sample_names=None, lane_spans=None):
    from wb.model import LaneLayout, Sample, SampleGroup

    if not isinstance(lane_count, int) or isinstance(lane_count, bool) or lane_count < 1:
        raise ValueError("レーン数は1以上の整数で指定してください")
    if sample_names is not None and (not isinstance(sample_names, list) or len(sample_names) > lane_count or any(n is not None and not isinstance(n, str) for n in sample_names)):
        raise ValueError("試料名はレーン数以下のリストで指定してください")
    project = _load(project_dir)
    panels = [_get(project.panels, "panel_id", panel_id)] if panel_id else project.included_panels()
    if not panels:
        raise ValueError("対象の段がありません")
    if sample_names is not None:
        names = [(n.strip() or None) if n is not None else None for n in sample_names]
        names += [None] * (lane_count - len(names))
        gid = f"SG_{panel_id}" if panel_id else "SG_MAIN"
        group = next((g for g in project.sample_groups if g.sample_group_id == gid), None)
        if group is None:
            group = SampleGroup(sample_group_id=gid)
            project.sample_groups.append(group)
        group.lane_count = lane_count
        group.samples = [Sample(name=n) for n in names]
    for panel in panels:
        _get(project.membrane_regions, "region_id", panel.region_id).lane_count = lane_count
        if panel.display_geometry:
            layout = panel.display_geometry.lane_layout
            previous = layout.lane_count
            layout.lane_count = lane_count
            if layout.centers_x and len(layout.centers_x) != lane_count:
                layout.centers_x = []
            if previous != lane_count or not layout.boundaries:
                layout.boundaries = [i / lane_count for i in range(lane_count + 1)]
            spans = lane_spans if lane_spans is not None else (layout.spans if previous == lane_count else [])
            panel.display_geometry.lane_layout = LaneLayout.model_validate({**layout.model_dump(), "spans": spans})
        if sample_names is not None:
            panel.sample_group_id = gid
        elif panel.sample_group_id:
            group = _get(project.sample_groups, "sample_group_id", panel.sample_group_id)
            if group.lane_count != lane_count:
                group.samples = group.samples[:lane_count] + [Sample() for _ in range(max(0, lane_count - len(group.samples)))]
                group.lane_count = lane_count
    return _save(project_dir, project, expected_revision, "set_lanes")


def set_lane_boundaries(project_dir, panel_id, boundaries, expected_revision):
    from wb.model import LaneLayout

    project = _load(project_dir)
    panel = _get(project.panels, "panel_id", panel_id)
    if not panel.display_geometry or panel.display_geometry.lane_layout.lane_count is None:
        raise ValueError("レーン数を先に設定してください")
    old = panel.display_geometry.lane_layout
    if old.spans:
        raise ValueError("空きのあるレーン配置は lane_spans で各レーンの範囲を指定してください")
    panel.display_geometry.lane_layout = LaneLayout.model_validate({**old.model_dump(), "boundaries": boundaries})
    return _save(project_dir, project, expected_revision, "set_lane_boundaries")


def _evidence_summary(evidence):
    if isinstance(evidence, str) and evidence.strip():
        return evidence.strip()
    if isinstance(evidence, dict) and evidence:
        return json.dumps(evidence, ensure_ascii=False)
    raise ValueError("確認の根拠を指定してください")


def _confirm_candidate(transform, evidence):
    if transform.status == "candidate":
        transform.status = "confirmed"
        transform.evidence = evidence
        transform.revision += 1


def _find_or_register_marker_transform(project, root, image_set, reference_frame_id, evidence=None, confirm=False):
    from wb.rendering import frame_for_capture, load_source_pixels

    existing = next(
        (t for t in project.transforms if t.kind == "marker_to_reference" and t.to_frame_id == reference_frame_id),
        None,
    )
    if existing is not None:
        if confirm:
            _confirm_candidate(existing, evidence)
        return existing

    ref_frame = _get(project.frames, "frame_id", reference_frame_id)
    colorimetric = [c for c in project.captures if c.modality == "colorimetric"]
    if not colorimetric:
        return None
    candidate_sources = set(image_set.marker_source_candidate_ids) if image_set else set()
    ordered = sorted(
        colorimetric,
        key=lambda c: (c.presentation_source_asset_id not in candidate_sources, c.capture_id),
    )
    chosen_frame = None
    for cap in ordered:
        mf = frame_for_capture(project, cap.capture_id)
        if (mf.width, mf.height) == (ref_frame.width, ref_frame.height):
            chosen_frame = mf
            break
    if chosen_frame is None:
        return None

    affine = Affine.identity(chosen_frame.frame_id, reference_frame_id)
    try:
        from wb.propose import _components, _masks, _register

        marker_img = load_source_pixels(project, root, chosen_frame.source_asset_id)
        ref_img = load_source_pixels(project, root, ref_frame.source_asset_id)
        polys, _ = _components(marker_img)
        if polys:
            _, regmask = _masks(marker_img.shape[:2], polys)
            reg = _register(ref_img, marker_img, regmask, chosen_frame.frame_id, reference_frame_id)
            if reg is not None:
                affine = reg[0]
    except Exception:
        pass
    tid = P.stable_id("TR", "marker_to_reference", chosen_frame.frame_id, reference_frame_id)
    record = Transform.model_validate(
        affine.to_json(tid, "marker_to_reference", "confirmed" if confirm else "candidate", 1, evidence or "auto_marker")
    )
    project.transforms.append(record)
    return record


def _ensure_colorimetric_marker(project, project_dir, panel, evidence=None, confirm=False):
    import math
    import numpy as np
    from wb.geometry import polygon_within_frame
    from wb.model import MarkerRegistration
    from wb.propose import choose_ladder, detect_marker_lanes
    from wb.rendering import load_source_pixels

    dg = panel.display_geometry
    if not dg or not dg.crop_rect or not dg.reference_to_rectified_transform_id:
        return None
    root = P.locate(project_dir).parent
    region = _get(project.membrane_regions, "region_id", panel.region_id)
    image_set = next(
        (s for s in project.image_sets if s.image_set_id == (panel.image_set_id or region.image_set_id)),
        None,
    )
    reg = (
        next((r for r in project.marker_registrations if r.marker_registration_id == panel.marker_registration_id), None)
        if panel.marker_registration_id
        else None
    ) or next((r for r in project.marker_registrations if r.membrane_region_id == panel.region_id), None)

    marker_transform = (
        next((t for t in project.transforms if reg and t.transform_id == reg.transform_id and t.kind == "marker_to_reference"), None)
        or _find_or_register_marker_transform(project, root, image_set, dg.reference_frame_id, evidence, confirm)
    )
    if marker_transform is None:
        if confirm and reg and reg.marker_roi is None:
            panel.marker_registration_id = None
        return None

    if confirm:
        _confirm_candidate(marker_transform, evidence)
        if evidence is not None:
            marker_transform.evidence = evidence

    if reg and reg.marker_roi and reg.marker_roi.frame_id == marker_transform.from_frame_id:
        reg.transform_id = marker_transform.transform_id
        if confirm:
            reg.status = "confirmed"
        panel.marker_registration_id = reg.marker_registration_id
        return reg.marker_registration_id

    marker_frame = _get(project.frames, "frame_id", marker_transform.from_frame_id)
    d_record = _get(project.transforms, "transform_id", dg.reference_to_rectified_transform_id)
    d = Affine.from_json(d_record.model_dump())
    marker_to_rect = d @ Affine.from_json(marker_transform.model_dump())
    if region.geometry and region.geometry.frame_id == dg.reference_frame_id:
        rectpoly = d.apply(np.asarray(region.geometry.polygon, dtype=np.float64))
    else:
        rectpoly = Rect(**dg.crop_rect.model_dump()).corners()

    left, right = float(rectpoly[:, 0].min()), float(rectpoly[:, 0].max())
    poly_y0, poly_y1 = float(rectpoly[:, 1].min()), float(rectpoly[:, 1].max())
    lanes = None
    try:
        marker_img = load_source_pixels(project, root, marker_frame.source_asset_id)
        width = max(marker_frame.width, math.ceil(right) + 2)
        height = max(marker_frame.height, math.ceil(poly_y1) + 2)
        crop = dg.crop_rect
        prefer_x = (crop.x0 + crop.x1) / 2 if crop else None
        lanes = choose_ladder(marker_img, marker_to_rect, width, height, rectpoly, prefer_x) or detect_marker_lanes(
            marker_img, marker_to_rect, width, height, rectpoly, fallback=True
        )
    except Exception:
        lanes = None

    points = None
    candidates_x = (
        [(lanes["x0_rectified"], lanes["x1_rectified"])]
        if lanes
        else [(left, min(right, left + max(3.0, 0.14 * (right - left))))]
    )
    for x0, x1 in candidates_x:
        for shrink in (0.0, 0.5, 1.0, 2.0, 4.0):
            xx0, xx1 = x0 + shrink, x1 - shrink
            yy0, yy1 = poly_y0 + shrink, poly_y1 - shrink
            if xx1 - xx0 < 2.0 or yy1 - yy0 < 2.0:
                break
            pts = marker_to_rect.inverse().apply(Rect(xx0, yy0, xx1, yy1).corners())
            if polygon_within_frame(pts, marker_frame.width, marker_frame.height):
                points = pts.tolist()
                break
        if points is not None:
            break

    if points is None:
        if confirm:
            panel.marker_registration_id = None
        return None

    rid = reg.marker_registration_id if reg else P.stable_id("MARKER", region.region_id)
    updated = MarkerRegistration(
        marker_registration_id=rid,
        membrane_region_id=region.region_id,
        transform_id=marker_transform.transform_id,
        marker_roi=FramePolygon(frame_id=marker_frame.frame_id, polygon=points),
        status="confirmed" if confirm else "candidate",
    )
    project.marker_registrations = [r for r in project.marker_registrations if r.marker_registration_id != rid] + [updated]
    panel.marker_registration_id = rid
    for sibling in project.panels:
        if sibling.region_id == region.region_id and sibling.marker_registration_id in (None, rid):
            sibling.marker_registration_id = rid
    return rid


def _choose_best_exposure(project, root, image_set, d, rect, reference_frame_id):
    from wb.propose import _exposure
    from wb.rendering import exposure_to_reference, frame_for_capture, load_source_pixels

    if not image_set or not image_set.capture_ids:
        return None, None
    captures = [c for c in (_get(project.captures, "capture_id", cid) for cid in image_set.capture_ids)]
    registered = []
    for cap in captures:
        try:
            tr = exposure_to_reference(project, cap.capture_id, reference_frame_id)
            registered.append((cap, frame_for_capture(project, cap.capture_id), tr))
        except Exception:
            continue
    if not registered:
        chosen = max(captures, key=lambda c: (c.exposure_s or 0, c.capture_id))
        return chosen.capture_id, "longest available exposure in image set"
    try:
        cache = {}

        def load_img(cap):
            if cap.capture_id not in cache:
                cache[cap.capture_id] = load_source_pixels(project, root, cap.presentation_source_asset_id)
            return cache[cap.capture_id]

        exp_id, reason, _ = _exposure(rect, d, registered, load_img)
        return exp_id, reason
    except Exception:
        chosen = max((c for c, _, _ in registered), key=lambda c: (c.exposure_s or 0, c.capture_id))
        return chosen.capture_id, "longest registered exposure"


def _ensure_series_transforms(project, image_set, reference_frame_id):
    from wb.rendering import frame_for_capture

    ref_frame = _get(project.frames, "frame_id", reference_frame_id)
    for cid in image_set.capture_ids:
        frame = frame_for_capture(project, cid)
        if frame.frame_id == reference_frame_id:
            continue
        has_tr = any(
            t.kind == "exposure_to_reference"
            and t.from_frame_id == frame.frame_id
            and t.to_frame_id == reference_frame_id
            for t in project.transforms
        )
        if not has_tr and (frame.width, frame.height) == (ref_frame.width, ref_frame.height):
            tid = P.stable_id("TR", "exposure_to_reference", frame.frame_id, reference_frame_id)
            project.transforms.append(
                Transform.model_validate(
                    Affine.identity(frame.frame_id, reference_frame_id).to_json(
                        tid, "exposure_to_reference", "candidate", 1, "identity_fallback"
                    )
                )
            )


def _next_membrane_label(project):
    used = {r.user_label for r in project.membrane_regions} | {r.region_id.removeprefix("REGION_") for r in project.membrane_regions}
    n = 1
    while f"M{n}" in used:
        n += 1
    return f"M{n}"


def _next_panel_label(project):
    labels = {p.label for p in project.panels if p.label}
    for code in range(ord("A"), ord("Z") + 1):
        if chr(code) not in labels:
            return chr(code)
    n = 1
    while f"P{n}" in labels:
        n += 1
    return f"P{n}"


def default_reference_frame(project, image_set_id=None):
    """``(image_set_id, frame_id)`` that a manually added membrane is drawn on.

    The frame of the Selection already in use, else the series reference, else its
    longest exposure: the same frame ``wbm overview`` shows by default.
    """
    from wb.rendering import frame_for_capture

    active_panel = next((p for p in _ordered_panels(project) if p.included and p.display_geometry), None) or (
        project.panels[0] if project.panels else None
    )
    active_region = (
        _get(project.membrane_regions, "region_id", active_panel.region_id)
        if active_panel
        else (project.membrane_regions[0] if project.membrane_regions else None)
    )
    if image_set_id is None:
        image_set_id = (
            (active_panel.image_set_id if active_panel else None)
            or (active_region.image_set_id if active_region else None)
            or (project.image_sets[0].image_set_id if project.image_sets else None)
        )
    if not image_set_id:
        raise ValueError("撮影系列が見つかりません")
    image_set = _get(project.image_sets, "image_set_id", image_set_id)
    frame_id = (
        (active_panel.display_geometry.reference_frame_id if active_panel and active_panel.display_geometry else None)
        or (active_region.reference_frame_id if active_region else None)
        or image_set.reference_frame_id
    )
    if frame_id is None:
        captures = [_get(project.captures, "capture_id", cid) for cid in image_set.capture_ids]
        longest = max(captures, key=lambda c: (c.exposure_s or 0, c.capture_id))
        frame_id = frame_for_capture(project, longest.capture_id).frame_id
    return image_set_id, frame_id


def guide_image(project_dir, frame_id=None, grid=None, stretch=True):
    """Write the whole image with a coordinate grid and the current membranes and Selections.

    For an agent that has to place membranes itself (``wbm overview``): read positions off the
    grid, ``add-membrane`` them on the returned ``frame_id``, then render again to check.
    """
    import cv2
    from wb.rendering import render_guide

    project = _load(project_dir)
    root = P.locate(project_dir).parent
    _, reference_id = default_reference_frame(project)
    frame_id = frame_id or reference_id
    frame = _get(project.frames, "frame_id", frame_id)
    shapes, membranes, selections = [], [], []
    for region in project.membrane_regions:
        if region.geometry and region.geometry.frame_id == frame_id:
            polygon = [list(map(float, pt)) for pt in region.geometry.polygon]
            shapes.append((region.user_label or region.region_id, polygon, (0, 200, 120)))
            membranes.append({"label": region.user_label, "included": region.included, "polygon": polygon})
    for panel in _ordered_panels(project):
        dg = panel.display_geometry
        if not dg or not dg.crop_rect or dg.reference_frame_id != frame_id:
            continue
        transform = _get(project.transforms, "transform_id", dg.reference_to_rectified_transform_id)
        crop = dg.crop_rect
        polygon = Affine.from_json(transform.model_dump()).inverse().apply(Rect(crop.x0, crop.y0, crop.x1, crop.y1).corners())
        polygon = [[round(float(x), 1), round(float(y), 1)] for x, y in polygon]
        shapes.append((panel.label, polygon, (255, 120, 0)))
        selections.append({"label": panel.label, "panel_id": panel.panel_id, "included": panel.included, "polygon": polygon})
    canvas, scale, step = render_guide(project, root, frame_id, shapes, grid=grid, stretch=stretch)
    out = P.cache_dir(project_dir) / "overviews" / f"guide-{frame_id}.png"
    if not cv2.imwrite(str(out), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)):
        raise ValueError(f"画像を書き出せませんでした: {out}")
    frames = []
    for capture in project.captures:
        f = next((x for x in project.frames if x.source_asset_id == capture.presentation_source_asset_id), None)
        if f:
            frames.append({"frame_id": f.frame_id, "capture_id": capture.capture_id, "modality": capture.modality,
                           "exposure_s": capture.exposure_s})
    return {
        "path": str(out),
        "frame_id": frame_id,
        "is_reference": frame_id == reference_id,
        "width": frame.width,
        "height": frame.height,
        "grid_px": step,
        "image_scale": round(scale, 4),
        "membranes": membranes,
        "selections": selections,
        "frames": frames,
    }


def add_manual_membrane(
    project_dir,
    expected_revision,
    rect=None,
    polygon=None,
    reference_frame_id=None,
    image_set_id=None,
    rotation_deg=0.0,
    user_label=None,
    label=None,
    create_selection=True,
):
    import math
    import numpy as np
    from wb.geometry import polygon_within_frame
    from wb.model import MembraneRegion, Panel
    from wb.propose import detect_marker_lanes
    from wb.rendering import frame_for_capture, load_source_pixels

    project = _load(project_dir)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    root = P.locate(project_dir).parent

    image_set_id, default_frame_id = default_reference_frame(project, image_set_id)
    image_set = _get(project.image_sets, "image_set_id", image_set_id)
    reference_frame_id = reference_frame_id or default_frame_id
    ref_frame = _get(project.frames, "frame_id", reference_frame_id)
    _ensure_series_transforms(project, image_set, reference_frame_id)

    if polygon is not None:
        pts = np.asarray(polygon, dtype=np.float64)
        if pts.shape != (4, 2):
            raise ValueError("polygonは4頂点の座標で指定してください")
    elif rect is not None:
        r = Rect(float(rect["x0"]), float(rect["y0"]), float(rect["x1"]), float(rect["y1"]))
        if (r.x1 - r.x0) < 6 or (r.y1 - r.y0) < 6:
            raise ValueError("メンブレンの範囲が小さすぎます")
        pts = r.corners()
    else:
        raise ValueError("rectまたはpolygonを指定してください")

    require_within_frame(pts, ref_frame.width, ref_frame.height)
    mem_label = (user_label.strip() if isinstance(user_label, str) and user_label.strip() else None) or _next_membrane_label(project)
    pan_label = (label.strip() if isinstance(label, str) and label.strip() else None) or _next_panel_label(project)

    rid = f"REGION_{mem_label}"
    suffix = 1
    while any(r.region_id == rid for r in project.membrane_regions):
        suffix += 1
        rid = f"REGION_{mem_label}_{suffix}"
    pid = f"PANEL_{rid}_{pan_label}"
    p_suffix = 1
    while any(p.panel_id == pid for p in project.panels):
        p_suffix += 1
        pid = f"PANEL_{rid}_{pan_label}_{p_suffix}"

    inherited_lanes = next(
        (
            _get(project.membrane_regions, "region_id", p.region_id).lane_count
            for p in _ordered_panels(project)
            if p.included and _get(project.membrane_regions, "region_id", p.region_id).lane_count
        ),
        None,
    )
    inherited_group = next((p.sample_group_id for p in _ordered_panels(project) if p.included and p.sample_group_id), None)

    region = MembraneRegion(
        region_id=rid,
        user_label=mem_label,
        target_label=pan_label,
        reference_frame_id=reference_frame_id,
        image_set_id=image_set_id,
        geometry=FramePolygon(frame_id=reference_frame_id, polygon=pts.tolist()),
        lane_count=inherited_lanes,
        region_assignment_status="proposed",
        created_by="human",
    )
    project.membrane_regions.append(region)
    if not create_selection:
        return _save(project_dir, project, expected_revision, "add_manual_membrane", mem_label)


    center = pts.mean(axis=0).tolist()
    angle = float(rotation_deg or 0.0)
    tid = "TR_D_" + pid
    rectified_id = "RECT_" + pid
    d = Affine.rotation_about(center, angle, reference_frame_id, rectified_id)
    project.transforms.append(
        Transform.model_validate(
            d.to_json(tid, "reference_to_rectified", "confirmed", 1, {"center": center, "created_by": "human"})
        )
    )

    rect_pts = d.apply(pts)
    rx0, ry0 = float(rect_pts[:, 0].min()), float(rect_pts[:, 1].min())
    rx1, ry1 = float(rect_pts[:, 0].max()), float(rect_pts[:, 1].max())
    # Shrink slightly if rotated corners exceed the frame
    crop_box = None
    for shrink in (0.0, 0.5, 1.0, 2.0, 4.0, 8.0):
        if rx1 - rx0 <= 2 * shrink + 4 or ry1 - ry0 <= 2 * shrink + 4:
            break
        candidate_box = Rect(rx0 + shrink, ry0 + shrink, rx1 - shrink, ry1 - shrink)
        if polygon_within_frame(d.inverse().apply(candidate_box.corners()), ref_frame.width, ref_frame.height):
            crop_box = candidate_box
            break
    if crop_box is None:
        raise ValueError("指定された範囲と角度が画像枠外にはみ出しています")

    # If a distinct colorimetric marker lane is detected on left/right edge of a wide membrane, trim sample x0..x1 cleanly
    marker_tr = _find_or_register_marker_transform(project, root, image_set, reference_frame_id, confirm=False)
    if marker_tr is not None and (crop_box.x1 - crop_box.x0) >= 80:
        try:
            mf = _get(project.frames, "frame_id", marker_tr.from_frame_id)
            m_img = load_source_pixels(project, root, mf.source_asset_id)
            lanes = detect_marker_lanes(
                m_img,
                d @ Affine.from_json(marker_tr.model_dump()),
                max(mf.width, math.ceil(rx1) + 2),
                max(mf.height, math.ceil(ry1) + 2),
                rect_pts,
                fallback=False,
            )
            if lanes:
                lx0, lx1 = lanes["x0_rectified"], lanes["x1_rectified"]
                if abs(lx0 - rx0) < abs(rx1 - lx1) and crop_box.x1 - lx1 >= 30:
                    trimmed = Rect(max(crop_box.x0, lx1 + 1.0), crop_box.y0, crop_box.x1, crop_box.y1)
                    if polygon_within_frame(d.inverse().apply(trimmed.corners()), ref_frame.width, ref_frame.height):
                        crop_box = trimmed
                elif crop_box.x0 < lx0 - 30:
                    trimmed = Rect(crop_box.x0, crop_box.y0, min(crop_box.x1, lx0 - 1.0), crop_box.y1)
                    if polygon_within_frame(d.inverse().apply(trimmed.corners()), ref_frame.width, ref_frame.height):
                        crop_box = trimmed
        except Exception:
            pass

    exp_id, exp_reason = _choose_best_exposure(project, root, image_set, d, crop_box, reference_frame_id)
    dg = DisplayGeometry(
        reference_frame_id=reference_frame_id,
        rectified_frame_id=rectified_id,
        reference_to_rectified_transform_id=tid,
        crop_rect=crop_box.__dict__,
        target_width_px=600,
        lane_layout={"lane_count": inherited_lanes, "centers_x": [], "status": "proposed"},
    )
    panel = Panel(
        panel_id=pid,
        region_id=rid,
        label=pan_label,
        image_set_id=image_set_id,
        sample_group_id=inherited_group,
        display_order=max((p.display_order or 0 for p in project.panels), default=0) + 1,
        selected_exposure_id=exp_id,
        exposure_reason=exp_reason,
        display_geometry=dg,
        review_status="proposed",
    )
    project.panels.append(panel)
    _ensure_colorimetric_marker(project, project_dir, panel, confirm=False)
    project = type(project).model_validate(project.model_dump())
    return _save(project_dir, project, expected_revision, "add_manual_membrane", f"{mem_label} ({pan_label})")


def add_panel_selection(project_dir, panel_id, crop_rect, expected_revision, rotation_deg=None, label=None, region_id=None):
    import math
    import numpy as np

    project = _load(project_dir)
    if project.project_revision != expected_revision:
        raise P.RevisionConflict(f"現在の版は{project.project_revision}です")
    root = P.locate(project_dir).parent
    source = _get(project.panels, "panel_id", panel_id) if panel_id else None
    region = _get(project.membrane_regions, "region_id", source.region_id if source else region_id)
    src_dg = source.display_geometry if source else DisplayGeometry(
        reference_frame_id=region.reference_frame_id or region.geometry.frame_id,
        target_width_px=600, lane_layout={"lane_count": region.lane_count})
    if not src_dg:
        raise ValueError("メンブレンの基準画像が設定されていません")
    panel_id = panel_id or f"PANEL_{region.region_id}"

    ids = {p.panel_id for p in project.panels}
    n = 1
    while f"{panel_id}_SEL{n}" in ids or any(t.transform_id == f"TR_D_{panel_id}_SEL{n}" for t in project.transforms):
        n += 1
    new_id = f"{panel_id}_SEL{n}"
    pan_label = (label.strip() if isinstance(label, str) and label.strip() else None) or _next_panel_label(project)

    src_tr = _get(project.transforms, "transform_id", src_dg.reference_to_rectified_transform_id) if src_dg.reference_to_rectified_transform_id else None
    current_angle = math.degrees(math.atan2(src_tr.matrix[1][0], src_tr.matrix[0][0])) if src_tr else 0
    ev = src_tr.evidence if src_tr and isinstance(src_tr.evidence, dict) else {}
    center = ev.get("center") or ev.get("rotation_center")
    if center is None:
        center = (
            list(np.asarray(region.geometry.polygon, dtype=float).mean(axis=0))
            if region.geometry
            else [0.0, 0.0]
        )
    angle = current_angle if rotation_deg is None else float(rotation_deg)
    rect = Rect(
        float(crop_rect["x0"]),
        float(crop_rect["y0"]),
        float(crop_rect["x1"]),
        float(crop_rect["y1"]),
    )
    if (rect.x1 - rect.x0) < 4 or (rect.y1 - rect.y0) < 4:
        raise ValueError("セレクションの範囲が小さすぎます")
    ref_frame = _get(project.frames, "frame_id", src_dg.reference_frame_id)
    tid = "TR_D_" + new_id
    rectified_id = "RECT_" + new_id
    d = Affine.rotation_about(center, angle, src_dg.reference_frame_id, rectified_id)
    require_within_frame(d.inverse().apply(rect.corners()), ref_frame.width, ref_frame.height)
    project.transforms.append(
        Transform.model_validate(d.to_json(tid, "reference_to_rectified", "confirmed", 1, {"center": list(center)}))
    )

    image_set = next(
        (s for s in project.image_sets if s.image_set_id == ((source.image_set_id if source else None) or region.image_set_id)),
        None,
    )
    exp_id, exp_reason = _choose_best_exposure(project, root, image_set, d, rect, src_dg.reference_frame_id)
    from wb.model import Panel
    panel = source.model_copy(deep=True) if source else Panel(panel_id=new_id, region_id=region.region_id, label=pan_label, image_set_id=region.image_set_id)
    panel.panel_id = new_id
    panel.label = pan_label
    panel.included = True
    panel.review_status = "proposed"
    panel.selected_band_row_candidate_id = None
    panel.band_annotation_ids = []
    panel.background_roi_ids = []
    panel.selected_exposure_id = exp_id or (source.selected_exposure_id if source else None)
    panel.exposure_reason = exp_reason or (source.exposure_reason if source else None)
    panel.display_geometry = DisplayGeometry(
        reference_frame_id=src_dg.reference_frame_id,
        rectified_frame_id=rectified_id,
        reference_to_rectified_transform_id=tid,
        crop_rect=rect.__dict__,
        target_width_px=src_dg.target_width_px or 600,
        lane_layout=src_dg.lane_layout.model_dump() if src_dg.lane_layout else {},
    )

    ordered = _ordered_panels(project)
    ordered.insert(ordered.index(source) + 1 if source else len(ordered), panel)
    project.panels.append(panel)
    for order, item in enumerate(ordered, 1):
        item.display_order = order
    _ensure_colorimetric_marker(project, project_dir, panel, confirm=False)
    return _save(project_dir, project, expected_revision, "add_panel_selection", f"{new_id} ({pan_label})")


def _approve(project, panel, evidence, all_transforms=False, project_dir=None):
    from wb.rendering import frame_for_capture

    panel.review_status = "confirmed"
    region = _get(project.membrane_regions, "region_id", panel.region_id)
    if region.geometry and region.image_set_id:
        region.region_assignment_status = "confirmed"
    dg = panel.display_geometry
    if dg and dg.reference_to_rectified_transform_id:
        transform = _get(project.transforms, "transform_id", dg.reference_to_rectified_transform_id)
        _confirm_candidate(transform, evidence)
        transform.evidence = evidence
    if dg:
        captures = [panel.selected_exposure_id] if panel.selected_exposure_id else []
        if all_transforms:
            image_set = next((s for s in project.image_sets if s.image_set_id == (panel.image_set_id or region.image_set_id)), None)
            captures = image_set.capture_ids if image_set else captures
        frames = {frame_for_capture(project, cid).frame_id for cid in captures}
        for transform in project.transforms:
            if transform.kind == "exposure_to_reference" and transform.from_frame_id in frames and transform.to_frame_id == dg.reference_frame_id:
                _confirm_candidate(transform, evidence)
    if all_transforms and dg:
        image_set = next((s for s in project.image_sets if s.image_set_id == (panel.image_set_id or region.image_set_id)), None)
        marker_sources = set(image_set.marker_source_candidate_ids) if image_set else set()
        marker_frames = {f.frame_id for f in project.frames if f.source_asset_id in marker_sources}
        marker_frames.update(
            reg.marker_roi.frame_id for reg in project.marker_registrations
            if reg.membrane_region_id == panel.region_id and reg.marker_roi
        )
        for transform in project.transforms:
            if transform.kind == "marker_to_reference" and transform.from_frame_id in marker_frames and transform.to_frame_id == dg.reference_frame_id:
                _confirm_candidate(transform, evidence)
    if project_dir is not None:
        _ensure_colorimetric_marker(project, project_dir, panel, evidence=evidence, confirm=True)
    elif panel.marker_registration_id:
        reg = _get(project.marker_registrations, "marker_registration_id", panel.marker_registration_id)
        if reg.marker_roi and reg.transform_id:
            transform = _get(project.transforms, "transform_id", reg.transform_id)
            if transform.kind != "marker_to_reference":
                raise ValueError("マーカーの位置合わせが不正です")
            _confirm_candidate(transform, evidence)
            transform.evidence = evidence
            reg.status = "confirmed"
        elif reg.marker_roi is None and reg.status == "pending":
            if reg.transform_id:
                transform = _get(project.transforms, "transform_id", reg.transform_id)
                if transform.kind == "marker_to_reference":
                    _confirm_candidate(transform, evidence)
            panel.marker_registration_id = None


def approve_panel(project_dir, panel_id, evidence, expected_revision):
    summary = _evidence_summary(evidence)
    project = _load(project_dir)
    _approve(project, _get(project.panels, "panel_id", panel_id), evidence, project_dir=project_dir)
    return _save(project_dir, project, expected_revision, "approve_panel", summary)


def approve_all(project_dir, evidence, expected_revision):
    summary = _evidence_summary(evidence)
    project = _load(project_dir)
    for panel in project.panels:
        if panel.included:
            _approve(project, panel, evidence, all_transforms=True, project_dir=project_dir)
    return _save(project_dir, project, expected_revision, "approve_all", summary)
