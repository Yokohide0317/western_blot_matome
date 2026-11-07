"""Shared figure layout in inches, calculated without loading source images."""

from __future__ import annotations

from dataclasses import dataclass
import math
import unicodedata

from wb.model import Panel, Project

SLIDE_W = 13.333
SLIDE_H = 7.5
LEFT = 0.4
RIGHT = 0.4
TOP = 0.35
BOTTOM = 0.3
LABEL_W = 1.2
LABEL_MAX_W = 3.0
LABEL_FONT_PT = 14
MW_W = 0.7
MARKER_MIN_W = 0.8
TABLE_ROW_H = 0.32
ROW_GAP = 0.14
PLACEHOLDER_H = 0.6


def label_width(labels) -> float:
    """Label column wide enough for the longest label, between LABEL_W and LABEL_MAX_W inches."""

    def text_w(text: str) -> float:
        ems = sum(1.0 if unicodedata.east_asian_width(ch) in "WFA" else 0.62 for ch in text)
        return ems * LABEL_FONT_PT / 72

    widest = max((text_w(label) for label in labels if label), default=0.0)
    return min(LABEL_MAX_W, max(LABEL_W, widest + 0.25))


@dataclass(frozen=True)
class _Size:
    width: int
    height: int
    content_height: float


@dataclass(frozen=True)
class _Row:
    panel: Panel
    size: _Size | None
    marker: _Size | None
    placeholder: str | None
    strips: dict | None = None


def _panel_size(panel: Panel) -> _Size | None:
    dg = panel.display_geometry
    if dg is None or dg.crop_rect is None or dg.target_width_px is None:
        return None
    crop = dg.crop_rect
    scale = dg.target_width_px / (crop.x1 - crop.x0)
    # Match geometry.panel_transform, including its bottom fractional-pixel padding.
    content_height = (crop.y1 - crop.y0) * scale
    return _Size(dg.target_width_px, math.ceil(content_height), content_height)


def _marker_size(project: Project, panel: Panel, size: _Size) -> _Size | None:
    from wb.rendering import marker_rect

    rect = marker_rect(project, panel)
    if rect is None:
        return None
    crop = panel.display_geometry.crop_rect
    width = math.ceil(float(rect.x1 - rect.x0) * size.width / (crop.x1 - crop.x0))
    return _Size(width, size.height, size.content_height) if width > 0 else None


def _rows(project: Project, pptx_markers: bool = False) -> list[_Row]:
    strips_for = None
    if pptx_markers:
        from wb.rendering import marker_export_rects

        strips_for = marker_export_rects
    panels = sorted(project.included_panels(), key=lambda p: p.display_order if p.display_order is not None else math.inf)
    rows = []
    for panel in panels:
        size = _panel_size(panel)
        if size is None:
            rows.append(_Row(panel, None, None, "未設定"))
        elif panel.selected_exposure_id is None:
            rows.append(_Row(panel, None, None, "採用露光が未選択"))
        else:
            strips = strips_for(project, panel) if strips_for else None
            rows.append(_Row(panel, size, _marker_size(project, panel, size), None, strips))
    return rows


def compute_layout(project: Project, pptx_markers: bool = False) -> dict:
    """Return JSON-serializable pages of flat elements using the adopted state.

    Images reference panel/capture IDs, never paths.
    The same coordinates are used for preview and output.
    ``pptx_markers=True`` replaces each row's single marker image with the
    export marker strip (the same range, rendered as an export part) and is
    used only by the PPTX exporter.
    """
    rows = _rows(project, pptx_markers)
    groups = {g.sample_group_id: g for g in project.sample_groups}
    regions = {r.region_id: r for r in project.membrane_regions}
    label_w = label_width(
        row.panel.label if row.panel.label is not None else regions[row.panel.region_id].user_label for row in rows
    )
    top = TOP
    bottom = SLIDE_H - BOTTOM

    def lane_units(row: _Row) -> float | None:
        layout = row.panel.display_geometry.lane_layout if row.panel.display_geometry else None
        group = groups.get(row.panel.sample_group_id)
        count = (layout.lane_count if layout else None) or (group.lane_count if group else None)
        if count is None:
            return None
        # Only explicit lane assignments set the scale. Unoccupied gaps and
        # outer margins retain their width without becoming sample lanes.
        occupied = sum(end - start for start, end in layout.spans) if layout and layout.spans else 1.0
        return count / occupied

    units = {row.panel.panel_id: lane_units(row) for row in rows}
    largest = max((value for value in units.values() if value is not None), default=1.0)
    factors = {row.panel.panel_id: (units[row.panel.panel_id] or largest) / largest for row in rows}

    def marker_ratio(row: _Row) -> float:
        if row.strips:
            crop = row.panel.display_geometry.crop_rect
            return sum(r.x1 - r.x0 for r in row.strips.values()) / (crop.x1 - crop.x0)
        if row.marker and row.size:
            return row.marker.width / row.size.width
        return 0

    ratio = max((marker_ratio(r) * factors[r.panel.panel_id] for r in rows), default=0)
    fixed_w = SLIDE_W - LEFT - RIGHT - label_w - MW_W

    def solve(max_lane_w: float = math.inf) -> tuple[float, float]:
        lane_w = min(fixed_w / (1 + ratio), fixed_w - MARKER_MIN_W, max_lane_w)
        return lane_w, max(MARKER_MIN_W, lane_w * ratio)

    lane_w, marker_w = solve()
    tallest = max((lane_w * factors[r.panel.panel_id] * r.size.height / r.size.width for r in rows if r.size), default=0)
    max_row_h = bottom - top - TABLE_ROW_H - ROW_GAP
    if tallest > max_row_h:
        lane_w, marker_w = solve(lane_w * max_row_h / tallest)
    lane_x = LEFT + label_w
    marker_x = lane_x + lane_w
    mw_x = marker_x + marker_w
    pages = []
    warnings = []
    warnings.extend({"panel_id": row.panel.panel_id,
                     "text": f"レーン数が未設定のため、{row.panel.panel_id} はレーン幅の統一対象に含めていません"}
                    for row in rows if units[row.panel.panel_id] is None)
    warned = set()

    def new_page() -> dict:
        page = {"index": len(pages), "elements": []}
        pages.append(page)
        return page

    page = new_page()
    y = top
    last_group = None
    last_bounds = None
    row_count = 0
    for row in rows:
        panel = row.panel
        row_w = lane_w * factors[panel.panel_id]
        row_marker_x = lane_x + row_w
        group = groups.get(panel.sample_group_id)
        gid = group.sample_group_id if group else None
        has_table = group is not None and group.lane_count is not None
        if group is not None and not has_table and gid not in warned:
            warned.add(gid)
            warnings.append({"group_id": gid, "text": f"レーン数が未確定のため、サンプル表 {gid} は作成していません"})
        bounds = None
        if has_table:
            layout_obj = panel.display_geometry.lane_layout if panel.display_geometry else None
            saved = layout_obj.boundaries if layout_obj else None
            bounds = list(saved) if saved and len(saved) == group.lane_count + 1 else [
                i / group.lane_count for i in range(group.lane_count + 1)
            ]
            spans = list(layout_obj.spans) if layout_obj and layout_obj.spans else list(zip(bounds, bounds[1:]))
            bounds = [list(span) for span in spans]
        h = row_w * row.size.height / row.size.width if row.size else PLACEHOLDER_H
        need_table = has_table and (gid != last_group or row_count == 0 or bounds != last_bounds)
        needed = h + (TABLE_ROW_H + ROW_GAP if need_table else 0)
        if row_count and y + needed > bottom + 1e-9:
            page = new_page()
            y = top
            row_count = 0
            need_table = has_table
        elements = page["elements"]
        if need_table:
            names = [s.name if s.name is not None else "" for s in group.samples]
            names += [""] * (group.lane_count - len(names))
            cells = []
            previous = 0.0
            for name, (start, end) in zip(names, spans):
                if start > previous:
                    cells.append((start - previous, "", True))
                cells.append((end - start, name, False))
                previous = end
            if previous < 1:
                cells.append((1 - previous, "", True))
            elements.append({
                "kind": "table", "group_id": gid, "x": LEFT, "y": y,
                "w": label_w + row_w, "h": TABLE_ROW_H, "label_w": label_w,
                "sample_x": lane_x, "sample_w": row_w, "columns": len(cells),
                "sample_lane_count": group.lane_count, "lane_spans": bounds,
                "column_w": row_w / group.lane_count,
                "column_widths": [row_w * width for width, _, _ in cells],
                "gap_columns": [i for i, (_, _, gap) in enumerate(cells) if gap],
                "names": [name for _, name, _ in cells], "font_size": 11,
            })
            last_bounds = bounds
            y += TABLE_ROW_H + ROW_GAP
        last_group = gid
        label = panel.label if panel.label is not None else regions[panel.region_id].user_label
        elements.append({
            "kind": "text", "role": "label", "panel_id": panel.panel_id,
            "x": LEFT, "y": y, "w": label_w - 0.1, "h": min(h, 0.5),
            "text": label, "font_size": LABEL_FONT_PT, "bold": True,
        })
        if row.size:
            common = {"kind": "image", "panel_id": panel.panel_id, "y": y,
                      "geometry_revision": panel.display_geometry.geometry_revision}
            elements.append({
                **common, "capture_id": panel.selected_exposure_id,
                "x": lane_x, "w": row_w, "h": h, "pixel_width": row.size.width,
                "pixel_height": row.size.height, "content_height": row.size.content_height,
            })
            if row.strips:
                # Every strip spans the band crop's exact height, so it sits level with the blot.
                crop = panel.display_geometry.crop_rect
                scale_px = row.size.width / (crop.x1 - crop.x0)
                k = row_w / row.size.width
                strip_x = row_marker_x
                for part in ("main",):
                    rect = row.strips.get(part)
                    if rect is None:
                        continue
                    strip_px_w = math.ceil((rect.x1 - rect.x0) * scale_px)
                    elements.append({
                        **common, "marker": True, "part": part,
                        "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "x": strip_x, "w": strip_px_w * k, "h": h,
                        "pixel_width": strip_px_w,
                        "pixel_height": row.size.height,
                        "content_height": row.size.content_height,
                    })
                    strip_x += strip_px_w * k
            elif row.marker and row.strips is None:
                k = row_w / row.size.width
                elements.append({
                    **common, "marker": True, "x": row_marker_x,
                    "w": row.marker.width * k, "h": h,
                    "pixel_width": row.marker.width, "pixel_height": row.marker.height,
                    "content_height": row.marker.content_height,
                })
        else:
            elements.append({
                "kind": "placeholder", "panel_id": panel.panel_id,
                "x": lane_x, "y": y, "w": row_w, "h": h,
                "text": row.placeholder, "font_size": 12, "color": "666666", "align": "center",
            })
        y += h + ROW_GAP
        row_count += 1

    return {
        "slide": {"w": SLIDE_W, "h": SLIDE_H}, "units": "inches",
        "pages": pages, "warnings": warnings,
        "figure_scale_mode": project.figure_scale_mode, "panel_width_factors": factors,
        "geometry": {"label_x": LEFT, "label_w": label_w, "lane_x": lane_x, "lane_w": lane_w,
                     "marker_x": marker_x, "marker_w": marker_w, "mw_x": mw_x, "mw_w": MW_W},
    }
