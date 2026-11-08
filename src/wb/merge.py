"""The whole, uncropped blot as chemiluminescence merged with the colorimetric marker.

Exported as an extra PPTX slide so a reader can check every crop against the
full membrane and its ladder (like a "Merge" view in the workbench).

One exposure is used for the whole image, chosen so that the bands of every
included Selection are clearly visible; a few saturated (red) pixels in the
strongest bands are accepted for that.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from wb.geometry import Affine, Rect, crop_polygon_in_source, warp
from wb.rendering import _find, exposure_to_reference, frame_for_capture, load_source_pixels

# A band row is "clearly visible" when its strongest pixels are this much darker than the
# crop's background (0–1 darkness). The shortest exposure reaching it everywhere is used.
VISIBLE = 0.6
# "A little red" is fine: at most this share of any crop may be saturated display pixels.
MAX_SATURATED = 0.02


def _red(image):
    return (image[..., 0] > 240) & (image[..., 1] < 20) & (image[..., 2] < 20)


@dataclass
class Merge:
    reference_frame_id: str
    capture_id: str
    exposure_s: float | None
    marker_frame_id: str | None
    reason: str
    png: bytes
    width: int
    height: int


def _stats(pixels, polygon):
    """``(visibility, saturated share)`` of a crop polygon in source pixels."""
    mask = np.zeros(pixels.shape[:2], np.uint8)
    cv2.fillPoly(mask, [np.rint(polygon - .5).astype(np.int32)], 1)
    inside = mask != 0
    if not inside.any():
        return 0.0, 0.0
    red = _red(pixels)
    darkness = 1 - cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    darkness[red] = 1
    values = darkness[inside]
    visibility = float(np.percentile(values, 98) - np.median(values))
    return visibility, float(red[inside].mean())


def choose_exposure(project, project_dir, panels, reference_frame_id, load=None):
    """``(capture_id, reason, stats)`` for one exposure that shows every panel's bands.

    ``stats`` maps capture_id to ``{panel_id: (visibility, saturated share)}``.
    """
    load = load or (lambda capture: load_source_pixels(project, project_dir, capture.presentation_source_asset_id))
    capture_ids = []
    for panel in panels:
        image_set = _find(project.image_sets, "image_set_id", panel.image_set_id)
        capture_ids += [c for c in image_set.capture_ids if c not in capture_ids]
    stats, exposure = {}, {}
    for capture_id in capture_ids:
        capture = _find(project.captures, "capture_id", capture_id)
        if capture.modality == "colorimetric":
            continue
        try:
            a = exposure_to_reference(project, capture_id, reference_frame_id)
        except Exception:
            continue
        pixels = load(capture)
        stats[capture_id] = {}
        for panel in panels:
            dg = panel.display_geometry
            d = Affine.from_json(_find(project.transforms, "transform_id", dg.reference_to_rectified_transform_id).model_dump())
            polygon = crop_polygon_in_source(a, d, Rect(**dg.crop_rect.model_dump()))
            stats[capture_id][panel.panel_id] = _stats(pixels, polygon)
        exposure[capture_id] = capture.exposure_s or 0
    if not stats:
        raise ValueError("全体画像に使える発光の画像がありません")
    chosen, reason = pick(stats, exposure)
    return chosen, reason, stats


def pick(stats, exposure):
    """``(capture_id, reason)`` from ``{capture_id: {panel_id: (visibility, saturated)}}`` and exposure seconds."""
    weakest = {c: min(v for v, _ in s.values()) for c, s in stats.items()}
    reddest = {c: max(r for _, r in s.values()) for c, s in stats.items()}
    usable = [c for c in stats if reddest[c] <= MAX_SATURATED]
    clear = [c for c in usable if weakest[c] >= VISIBLE]
    if clear:
        chosen = min(clear, key=lambda c: (exposure[c], c))
        reason = "shortest exposure where the bands of every Selection are clearly visible"
    elif usable:
        chosen = max(usable, key=lambda c: (weakest[c], -exposure[c], c))
        reason = "exposure that shows the weakest Selection best with little saturation"
    else:
        chosen = min(stats, key=lambda c: (reddest[c], -weakest[c], c))
        reason = "every exposure saturates a Selection; the least saturated one"
    return chosen, reason


def _marker_to_reference(project, panels, reference_frame_id):
    for panel in panels:
        reg = next((r for r in project.marker_registrations
                    if r.marker_registration_id == panel.marker_registration_id), None)
        if reg and reg.transform_id:
            t = next((t for t in project.transforms if t.transform_id == reg.transform_id), None)
            if t and t.kind == "marker_to_reference" and t.to_frame_id == reference_frame_id:
                return t
    return next((t for t in project.transforms
                 if t.kind == "marker_to_reference" and t.to_frame_id == reference_frame_id), None)


def render_merges(project, project_dir, skipped: list | None = None) -> list[Merge]:
    """One merged whole image per reference frame used by the included Selections.

    The merge is a supplement: a reference frame that cannot be merged is skipped (its reason
    appended to ``skipped``) and the figure itself is still exported.
    """
    panels = [p for p in project.included_panels() if p.display_geometry and p.display_geometry.crop_rect]
    groups: dict[str, list] = {}
    for panel in panels:
        groups.setdefault(panel.display_geometry.reference_frame_id, []).append(panel)
    merges = []
    for reference_frame_id, members in groups.items():
        try:
            merges.append(_render(project, project_dir, reference_frame_id, members))
        except Exception as exc:  # noqa: BLE001 - never block the figure export
            if skipped is not None:
                skipped.append(f"{reference_frame_id}: {type(exc).__name__}: {exc}")
    return merges


def _render(project, project_dir, reference_frame_id, members) -> Merge:
    ref = _find(project.frames, "frame_id", reference_frame_id)
    capture_id, reason, _ = choose_exposure(project, project_dir, members, reference_frame_id)
    capture = _find(project.captures, "capture_id", capture_id)
    source = frame_for_capture(project, capture_id)
    pixels = load_source_pixels(project, project_dir, source.source_asset_id)
    a = exposure_to_reference(project, capture_id, reference_frame_id)
    image = warp(pixels, a, ref.width, ref.height, border_value=(255, 255, 255)).astype(np.float32)
    marker = _marker_to_reference(project, members, reference_frame_id)
    marker_frame_id = None
    if marker is not None:
        frame = _find(project.frames, "frame_id", marker.from_frame_id)
        colour = load_source_pixels(project, project_dir, frame.source_asset_id)
        colour = warp(colour, Affine.from_json(marker.model_dump()), ref.width, ref.height,
                      border_value=(255, 255, 255)).astype(np.float32)
        # Multiply: dark bands stay dark, the ladder keeps its colours, white stays white.
        image = image * colour / 255
        marker_frame_id = frame.frame_id
    rgb = np.clip(np.rint(image), 0, 255).astype(np.uint8)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError("全体画像を作れませんでした")
    return Merge(reference_frame_id, capture_id, capture.exposure_s, marker_frame_id, reason,
                 encoded.tobytes(), ref.width, ref.height)
