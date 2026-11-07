import { apply } from './coordinates.js';

// Port of wb.geometry.marker_x_span: x extent of a rectified polygon within the slab y0..y1.
export function markerXSpan(points, y0, y1) {
  const clip = (pts, keep, cut) => {
    const out = [];
    pts.forEach((curr, i) => {
      const prev = pts[(i + pts.length - 1) % pts.length];
      if (keep(curr) !== keep(prev)) out.push(cut(prev, curr));
      if (keep(curr)) out.push(curr);
    });
    return out;
  };
  const at = y => (a, b) => [a[0] + (b[0] - a[0]) * ((y - a[1]) / (b[1] - a[1])), y];
  let slab = clip(points, p => p[1] >= y0 - 1e-9, at(y0));
  slab = clip(slab, p => p[1] <= y1 + 1e-9, at(y1));
  const xs = (slab.length >= 2 ? slab : points).map(p => p[0]);
  return [Math.min(...xs), Math.max(...xs)];
}

// The marker range in the Selection's rectified frame. Its height is always the band crop's;
// x is the saved range, else the registered ladder lane at crop height.
export function effectiveMarkerRect(project, panel) {
  const dg = panel?.display_geometry;
  if (!dg?.crop_rect) return null;
  if (dg.marker_x) return {x0: dg.marker_x.x0, x1: dg.marker_x.x1, y0: dg.crop_rect.y0, y1: dg.crop_rect.y1};
  const reg = project.marker_registrations.find(r => r.marker_registration_id === panel.marker_registration_id && r.membrane_region_id === panel.region_id);
  const toReference = project.transforms.find(t => t.transform_id === reg?.transform_id && t.kind === 'marker_to_reference');
  const toRectified = project.transforms.find(t => t.transform_id === dg.reference_to_rectified_transform_id);
  if (!reg?.marker_roi || !toReference || !toRectified || reg.marker_roi.frame_id !== toReference.from_frame_id) return null;
  const points = reg.marker_roi.polygon.map(p => apply(toRectified.matrix, apply(toReference.matrix, p)));
  const [x0, x1] = markerXSpan(points, dg.crop_rect.y0, dg.crop_rect.y1);
  return x1 > x0 ? {x0, x1, y0: dg.crop_rect.y0, y1: dg.crop_rect.y1} : null;
}

// Trim one side or move the whole range sideways. The vertical range belongs to the band crop.
export function dragMarker(kind, start, value, minWidth = 4) {
  if (kind === 'left') return {...start, x0: Math.min(value, start.x1 - minWidth)};
  if (kind === 'right') return {...start, x1: Math.max(value, start.x0 + minWidth)};
  if (kind === 'move') return {...start, x0: start.x0 + value, x1: start.x1 + value};
  return {...start};
}
