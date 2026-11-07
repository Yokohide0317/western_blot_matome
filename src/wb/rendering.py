"""Render source images in the project's edge-coordinate convention."""

from dataclasses import dataclass
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import cv2
import numpy as np
import tifffile

from wb import project as P
from wb.geometry import (
    Affine,
    Rect,
    crop_polygon_in_source,
    panel_transform,
    marker_panel_transform,
    marker_x_span,
    polygon_within_frame,
    require_within_frame,
    warp,
)

RENDERER_VERSION = "1.1"


class MissingTransform(ValueError):
    pass


@dataclass(frozen=True)
class RenderedImage:
    png: bytes
    width: int
    height: int
    content_height: float
    sha256: str
    cache_path: Path


def _find(items, key, value):
    return next(x for x in items if getattr(x, key) == value)


def frame_for_capture(project, capture_id):
    capture = _find(project.captures, "capture_id", capture_id)
    return _find(project.frames, "source_asset_id", capture.presentation_source_asset_id)


def load_source_pixels(project, project_dir, source_asset_id):
    source = _find(project.sources, "source_asset_id", source_asset_id)
    if not source.relative_path:
        raise ValueError(f"元画像のパスがありません: {source_asset_id}")
    path = Path(project_dir) / source.relative_path
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != source.sha256:
        raise ValueError(f"元画像のSHA-256が変わっています: {source_asset_id}")
    frame = _find(project.frames, "source_asset_id", source_asset_id)
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[frame.page_index or 0]
        pixels = page.asarray()
        if page.planarconfig == 2:
            pixels = np.moveaxis(pixels, 0, -1)
    mapping = getattr(source, "display_mapping", None)
    if mapping and pixels.dtype == np.uint16 and pixels.ndim == 2:
        gray = np.rint(np.clip((pixels.astype(np.float64) - mapping["lower"]) /
                              (mapping["upper"] - mapping["lower"]), 0, 1) * 255).astype(np.uint8)
        if mapping["invert"]:
            gray = 255 - gray
        rgb = np.repeat(gray[..., None], 3, axis=2)
        rgb[pixels == mapping["saturation_value"]] = (255, 0, 0)
        pixels = rgb
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError(f"表示用の元画像はRGBのuint8である必要があります: {source_asset_id}")
    if frame.width != pixels.shape[1] or frame.height != pixels.shape[0]:
        raise ValueError(f"元画像の寸法が変わっています: {source_asset_id}")
    return pixels


def exposure_to_reference(project, capture_id, reference_frame_id):
    frame = frame_for_capture(project, capture_id)
    if frame.frame_id == reference_frame_id:
        return Affine.identity(frame.frame_id)
    for t in project.transforms:
        if (
            t.kind == "exposure_to_reference"
            and t.from_frame_id == frame.frame_id
            and t.to_frame_id == reference_frame_id
        ):
            return Affine.from_json(t.model_dump())
    raise MissingTransform(f"{frame.frame_id} -> {reference_frame_id}")


def display_parts(project, panel):
    dg = panel.display_geometry
    if not dg or not dg.crop_rect or not dg.target_width_px:
        raise ValueError("表示範囲が未設定です")
    t = _find(project.transforms, "transform_id", dg.reference_to_rectified_transform_id)
    if (
        t.kind != "reference_to_rectified"
        or t.from_frame_id != dg.reference_frame_id
        or t.to_frame_id != dg.rectified_frame_id
    ):
        raise ValueError("基準画像から傾き補正画像への変換が不正です")
    return Affine.from_json(t.model_dump()), Rect(**dg.crop_rect.model_dump())


def _atomic_png(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _cached(project, project_dir, frame, params, draw, content_height):
    source = _find(project.sources, "source_asset_id", frame.source_asset_id)
    # Verify even on cache hits; a stale source must never silently reuse pixels.
    path = Path(project_dir) / source.relative_path
    if hashlib.sha256(path.read_bytes()).hexdigest() != source.sha256:
        raise ValueError(f"元画像のSHA-256が変わっています: {source.source_asset_id}")
    key_data = {
        "source": source.sha256,
        "page": frame.page_index,
        "channel": frame.channel_selection,
        "renderer_version": RENDERER_VERSION,
        "display_mapping": getattr(source, "display_mapping", None),
        **{k: v for k, v in params.items() if k != "output_name"},
    }
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cache = P.cache_dir(project_dir) / "cache" / (key + ".png")
    if cache.exists():
        png = cache.read_bytes()
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"キャッシュ画像を読み取れません: {cache}")
        height, width = image.shape[:2]
    else:
        image = draw(load_source_pixels(project, project_dir, frame.source_asset_id))
        height, width = image.shape[:2]
        options = [cv2.IMWRITE_PNG_COMPRESSION, params["png_compression"]] if "png_compression" in params else []
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), options)
        if not ok:
            raise ValueError("PNGへの変換に失敗しました")
        png = encoded.tobytes()
        cache.parent.mkdir(parents=True, exist_ok=True)
        _atomic_png(cache, png)
    readable = params.get("output_name")
    if readable:
        directory = "overviews" if params["kind"] == "overview" else "panels"
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", readable)
        output = P.cache_dir(project_dir) / directory / (name + ".png")
        if not output.exists() or output.read_bytes() != png:
            _atomic_png(output, png)
    return RenderedImage(png, width, height, content_height, hashlib.sha256(png).hexdigest(), cache)


def render_panel(project, project_dir, panel_id, capture_id):
    panel = _find(project.panels, "panel_id", panel_id)
    dg = panel.display_geometry
    d, crop = display_parts(project, panel)
    a = exposure_to_reference(project, capture_id, dg.reference_frame_id)
    frame = frame_for_capture(project, capture_id)
    require_within_frame(crop_polygon_in_source(a, d, crop), frame.width, frame.height)
    reference = _find(project.frames, "frame_id", dg.reference_frame_id)
    reference_source = _find(project.sources, "source_asset_id", reference.source_asset_id)
    if (
        not reference_source.relative_path
        or hashlib.sha256((Path(project_dir) / reference_source.relative_path).read_bytes()).hexdigest()
        != reference_source.sha256
    ):
        raise ValueError(f"元画像のSHA-256が変わっています: {reference.source_asset_id}")
    geom = panel_transform(a, d, crop, dg.target_width_px, "PANEL_" + panel_id)

    def draw(pixels):
        image = warp(pixels, geom.transform, geom.out_width, geom.out_height, border_value=(255, 255, 255))
        if geom.content_height < geom.out_height:
            image[int(geom.content_height) :] = 255
        return image

    return _cached(
        project,
        project_dir,
        frame,
        {
            "kind": "panel",
            "output_name": f"{panel_id}--{capture_id}",
            "geometry_revision": dg.geometry_revision,
            "matrix": geom.transform.matrix.tolist(),
            "size": [geom.out_width, geom.out_height],
            "content_height": geom.content_height,
        },
        draw,
        geom.content_height,
    )


def marker_parts(project, panel):
    """``(registration, marker_to_reference, reference_to_rectified, marker_frame)`` or None."""
    dg = panel.display_geometry
    if not dg or not dg.crop_rect:
        return None
    reg = next(
        (
            r
            for r in project.marker_registrations
            if r.marker_registration_id == panel.marker_registration_id and r.membrane_region_id == panel.region_id
        ),
        None,
    )
    if not reg or not reg.transform_id or not reg.marker_roi:
        return None
    t = next(
        (t for t in project.transforms if t.transform_id == reg.transform_id and t.kind == "marker_to_reference"), None
    )
    d_record = next((x for x in project.transforms if x.transform_id == dg.reference_to_rectified_transform_id), None)
    if not t or not d_record:
        return None
    frame = next((f for f in project.frames if f.frame_id == t.from_frame_id), None)
    if not frame or not frame.width or not frame.height or reg.marker_roi.frame_id != t.from_frame_id:
        return None
    return reg, Affine.from_json(t.model_dump()), Affine.from_json(d_record.model_dump()), frame


def marker_rect_fits(project, panel, rect):
    parts = marker_parts(project, panel)
    if parts is None:
        return False
    _, a, d, frame = parts
    return polygon_within_frame(crop_polygon_in_source(a, d, rect), frame.width, frame.height)


def marker_rect(project, panel):
    """The marker range in rectified coordinates, always over the band crop's exact height.

    x is the hand-set range when there is one, else the registered ladder lane.
    Sharing the crop's vertical range keeps marker and bands from drifting apart.
    """
    parts = marker_parts(project, panel)
    if parts is None:
        return None
    dg = panel.display_geometry
    crop = dg.crop_rect
    if dg.marker_x is not None:
        return Rect(dg.marker_x.x0, crop.y0, dg.marker_x.x1, crop.y1)
    reg, a, d, _ = parts
    mx0, mx1 = marker_x_span((d @ a).apply(reg.marker_roi.polygon), crop.y0, crop.y1)
    return Rect(mx0, crop.y0, mx1, crop.y1) if mx1 > mx0 else None


def marker_export_rects(project, panel):
    """Rectified-space marker strip for PPTX export, geometry only.

    Returns ``{'main': Rect}`` — the same marker range as the preview and PNG,
    over the band crop's exact height — or ``{}`` when it is unregistered or
    would need pixels outside the colorimetric frame (never fabricated).
    """
    main = marker_rect(project, panel)
    if main is None or not marker_rect_fits(project, panel, main):
        return {}
    return {"main": main}


def render_marker(project, project_dir, panel_id, part=None, rect=None):
    panel = _find(project.panels, "panel_id", panel_id)
    parts = marker_parts(project, panel)
    if parts is None:
        return {"status": "marker_registration_pending", "image": None}
    reg, a, d, frame = parts
    dg = panel.display_geometry
    crop = Rect(**dg.crop_rect.model_dump())
    scale = dg.target_width_px / (crop.x1 - crop.x0)
    params = {"geometry_revision": dg.geometry_revision}
    if part is None:
        marker_crop = marker_rect(project, panel)
        if marker_crop is None:
            return {"status": "marker_registration_pending", "image": None}
        params.update({"kind": "marker", "output_name": f"{panel_id}--marker",
                       "rect": [marker_crop.x0, marker_crop.y0, marker_crop.x1, marker_crop.y1]})
    else:
        available = marker_export_rects(project, panel)
        if part not in available:
            raise ValueError(f"marker export part is unavailable: {part}")
        marker_crop = Rect(*rect) if rect is not None else available[part]
        params.update({
            "kind": "marker_export", "part": part,
            "rect": [marker_crop.x0, marker_crop.y0, marker_crop.x1, marker_crop.y1],
        })
    geom = marker_panel_transform(a, d, marker_crop, scale, f"MARKER_{panel_id}:{part or 'preview'}")

    def draw(pixels):
        image = warp(pixels, geom.transform, geom.out_width, geom.out_height, border_value=(255, 255, 255))
        if geom.content_height < geom.out_height:
            image[int(geom.content_height) :] = 255
        return image

    image = _cached(
        project,
        project_dir,
        frame,
        {
            **params,
            "matrix": geom.transform.matrix.tolist(),
            "size": [geom.out_width, geom.out_height],
        },
        draw,
        geom.content_height,
    )
    return {"status": reg.status, "image": image}


def render_overview(project, project_dir, frame_id, max_width=1200):
    if max_width <= 0:
        raise ValueError("max_widthには正の整数を指定してください")
    frame = _find(project.frames, "frame_id", frame_id)
    scale = min(1.0, max_width / frame.width)
    width = max(1, round(frame.width * scale))
    height = max(1, round(frame.height * scale))
    compression = 9 if max_width <= 1200 else 3
    image = _cached(
        project,
        project_dir,
        frame,
        {
            "kind": "overview",
            "output_name": f"{frame_id}--w{max_width}",
            "max_width": max_width,
            "size": [width, height],
            "png_compression": compression,
        },
        lambda pixels: cv2.resize(pixels, (width, height), interpolation=cv2.INTER_AREA),
        float(height),
    )
    return image, scale
