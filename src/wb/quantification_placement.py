"""Initial, editable measurement boxes derived from the saved band selection.

Only coordinates are transformed. Measurements always use unchanged raw pixels.
"""
import math

import numpy as np

from wb.geometry import Affine, Rect
from wb import quantification as Q


def _selection_to_raw(project, panel, group):
    dg = panel.display_geometry
    if not dg or not dg.crop_rect:
        raise ValueError('バンドの選択範囲がありません')
    if dg.reference_to_rectified_transform_id:
        t = Q.find(project.transforms, 'transform_id', dg.reference_to_rectified_transform_id)
        if (t.kind, t.from_frame_id, t.to_frame_id) != ('reference_to_rectified', dg.reference_frame_id, dg.rectified_frame_id):
            raise ValueError('回転補正の座標対応が不正です')
        rectified_to_reference = Affine.from_json(t.model_dump()).inverse()
    elif dg.rectified_frame_id in (None, dg.reference_frame_id):
        rectified_to_reference = Affine.identity(dg.reference_frame_id)
    else:
        raise ValueError('回転補正の座標対応がありません')
    if dg.reference_frame_id == group.frame_id:
        return rectified_to_reference, False

    capture = Q.find(project.captures, 'capture_id', group.capture_id)
    frames = [f for f in project.frames if f.source_asset_id == capture.presentation_source_asset_id]
    if len(frames) != 1:
        raise ValueError('表示画像の座標を一意に指定できません')
    display = frames[0]
    if display.frame_id == dg.reference_frame_id:
        reference_to_display = Affine.identity(display.frame_id)
    else:
        transforms = [t for t in project.transforms if t.kind == 'exposure_to_reference'
                      and t.from_frame_id == display.frame_id and t.to_frame_id == dg.reference_frame_id]
        if len(transforms) != 1:
            raise ValueError('選んだ露光とバンド選択の位置合わせがありません')
        reference_to_display = Affine.from_json(transforms[0].model_dump()).inverse()

    estimated = False
    if display.frame_id == group.frame_id:
        display_to_raw = Affine.identity(group.frame_id)
    elif capture.analysis_to_presentation_transform_id:
        t = Q.find(project.transforms, 'transform_id', capture.analysis_to_presentation_transform_id)
        if (t.kind, t.from_frame_id, t.to_frame_id) != ('analysis_to_presentation', group.frame_id, display.frame_id):
            raise ValueError('表示画像とraw16の座標対応が不正です')
        display_to_raw = Affine.from_json(t.model_dump()).inverse()
    else:
        raw = Q.find(project.frames, 'frame_id', group.frame_id)
        if not all((raw.width, raw.height, display.width, display.height)):
            raise ValueError('表示画像とraw16の寸法が不明です')
        sx, sy = raw.width / display.width, raw.height / display.height
        if abs(sx / sy - 1) > .01:
            raise ValueError('表示画像とraw16の縦横比が異なります')
        # A linked filename pair supplies an estimate, not a verified alignment.
        display_to_raw = Affine(display.frame_id, raw.frame_id, [[sx, 0, 0], [0, sy, 0], [0, 0, 1]])
        estimated = True
    return display_to_raw @ reference_to_display @ rectified_to_reference, estimated


def _lane_intervals(layout, crop, count):
    if layout.lane_count not in (None, count):
        raise ValueError('レーン数が変わっています。定量グループを作成し直してください')
    if layout.spans:
        fractions = layout.spans
    elif layout.boundaries:
        fractions = list(zip(layout.boundaries, layout.boundaries[1:]))
    elif layout.centers_x:
        centers = layout.centers_x
        if len(centers) != count or any(b <= a for a, b in zip(centers, centers[1:])) or not all(crop.x0 < x < crop.x1 for x in centers):
            raise ValueError('レーン中心が選択範囲に収まりません')
        edges = [crop.x0, *[(a+b)/2 for a, b in zip(centers, centers[1:])], crop.x1]
        # Preserve the saved centers, even for nonuniform spacing.
        return [(x - min(x-a, b-x), x + min(x-a, b-x)) for x, a, b in zip(centers, edges, edges[1:])]
    else:
        fractions = [(i/count, (i+1)/count) for i in range(count)]
    return [(crop.x0 + a*(crop.x1-crop.x0), crop.x0 + b*(crop.x1-crop.x0)) for a, b in fractions]


def from_selection(project, panel, group, pixels):
    transform, estimated = _selection_to_raw(project, panel, group)
    crop = Rect(**panel.display_geometry.crop_rect.model_dump())
    intervals = _lane_intervals(panel.display_geometry.lane_layout, crop, len(group.lanes))
    centers, extents = [], []
    for left, right in intervals:
        cx, cy = (left+right)/2, (crop.y0+crop.y1)/2
        centers.append(transform.apply([cx, cy]))
        # Leave a margin inside each lane and band selection for later adjustment.
        points = transform.apply(Rect(left+.1*(right-left), crop.y0+.1*(crop.y1-crop.y0),
                                      right-.1*(right-left), crop.y1-.1*(crop.y1-crop.y0)).corners())
        extents.append(np.ptp(points, axis=0))
    centers = np.asarray(centers)
    h, w = pixels.shape
    if not np.isfinite(centers).all() or np.any(centers <= 0) or np.any(centers >= [w, h]):
        raise ValueError('選択したバンドがraw16の画像外にあります')
    width, height = map(int, np.floor(np.min(extents, axis=0)))
    width = min(width, math.floor(2*np.min(np.minimum(centers[:, 0], w-centers[:, 0]))))
    height = min(height, math.floor(2*np.min(np.minimum(centers[:, 1], h-centers[:, 1]))), h//2)
    if width < 1 or height < 1:
        raise ValueError('選択範囲が小さすぎるため定量枠を置けません')
    candidate = group.model_copy(deep=True)
    candidate.width_px, candidate.height_px = int(width), int(height)
    for lane, (cx, cy) in zip(candidate.lanes, centers):
        lane.x = min(w-width, max(0, int(math.floor(cx-width/2+.5))))
        lane.y = min(h-height, max(0, int(math.floor(cy-height/2+.5))))
    # Prefer a nearby background above/below each band, avoiding every band ROI.
    gap = max(1, height//4)
    for lane in candidate.lanes:
        neighbours = [b for b in candidate.lanes if lane.x < b.x+width and b.x < lane.x+width]
        ys = {0, h-height}
        for b in neighbours:
            ys.update((b.y-height-gap, b.y+height+gap, b.y-height, b.y+height))
        valid = [y for y in ys if 0 <= y <= h-height and all(y+height <= b.y or y >= b.y+height for b in neighbours)]
        if not valid:
            raise ValueError('バンドに重ならない背景枠を置けません')
        lane.background_x = lane.x
        lane.background_y = min(valid, key=lambda y: (abs(y-lane.y), y))
    Q.validate(pixels, candidate)
    candidate.results = None
    candidate.placement_method = 'estimated' if estimated else 'band_selection'
    candidate.placement_note = ('表示画像とraw16の寸法から位置を推定しました。画像上で枠を調整してください。' if estimated
                                else 'バンド選択から仮配置しました。画像上でバンドと背景の枠を調整してください。')
    return candidate


def initial(project, panel, group, pixels):
    try:
        return from_selection(project, panel, group, pixels)
    except ValueError as exc:
        reason = str(exc)
    # Keep quantification available when no usable selection/mapping exists.
    h, w = pixels.shape
    count = len(group.lanes)
    if h < 2 or w < count:
        raise ValueError('定量枠を置くには画像が小さすぎます')
    group.width_px, group.height_px = max(1, w//(count*3)), max(1, h//10)
    for i, lane in enumerate(group.lanes):
        lane.x = lane.background_x = min(w-group.width_px, int((i+.5)*w/count)-group.width_px//2)
        lane.y, lane.background_y = h//2-group.height_px//2, 0
    group.placement_method = 'fallback'
    group.placement_note = f'{reason}。仮の枠を配置しました。画像上で位置を合わせてください。'
    Q.validate(pixels, group)
    return group
