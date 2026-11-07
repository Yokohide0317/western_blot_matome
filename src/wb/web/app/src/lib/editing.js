import { rotation, cropPolygon } from './coordinates.js';

export const MIN_BAND_HEIGHT = 6;
export const MIN_BAND_WIDTH = 6;
// A hair of slack keeps clamped corners strictly inside after the server recomputes the rotation.
const SLACK = 1e-3;

export function insideFrame(polygon, frame) {
  return polygon.every(([x, y]) => x >= SLACK && x <= frame.width - SLACK && y >= SLACK && y <= frame.height - SLACK);
}
export const validCrop = (rect, angle, center, frame) => insideFrame(cropPolygon(rect, rotation(angle, center)), frame);

// Move from a valid value toward the target and stop at the last valid one.
export function clampTowards(current, target, isValid) {
  if (isValid(target)) return target;
  if (!isValid(current)) return current;
  let good = current, bad = target;
  for (let i = 0; i < 48; i++) {
    const middle = (good + bad) / 2;
    if (isValid(middle)) good = middle; else bad = middle;
  }
  return good;
}

// A band that starts thinner/narrower than the minimum may not shrink further, but is not forced to grow.
export const minHeight = live => Math.min(MIN_BAND_HEIGHT, live.bottom - live.top);
export const minWidth = live => Math.min(MIN_BAND_WIDTH, (live.right ?? live.rect.x1) - (live.left ?? live.rect.x0));

const currentX0 = live => live.left ?? live.rect.x0;
const currentX1 = live => live.right ?? live.rect.x1;

export function dragTop(target, live, center, frame) {
  const x0 = currentX0(live), x1 = currentX1(live);
  const wanted = Math.min(target, live.bottom - (live.min ?? MIN_BAND_HEIGHT));
  return clampTowards(live.top, wanted, y => validCrop({x0, x1, y0: y, y1: live.bottom}, live.angle, center, frame));
}
export function dragBottom(target, live, center, frame) {
  const x0 = currentX0(live), x1 = currentX1(live);
  const wanted = Math.max(target, live.top + (live.min ?? MIN_BAND_HEIGHT));
  return clampTowards(live.bottom, wanted, y => validCrop({x0, x1, y0: live.top, y1: y}, live.angle, center, frame));
}
export function dragLeft(target, live, center, frame) {
  const x0 = currentX0(live), x1 = currentX1(live);
  const wanted = Math.min(target, x1 - (live.minW ?? MIN_BAND_WIDTH));
  return clampTowards(x0, wanted, x => validCrop({x0: x, x1, y0: live.top, y1: live.bottom}, live.angle, center, frame));
}
export function dragRight(target, live, center, frame) {
  const x0 = currentX0(live), x1 = currentX1(live);
  const wanted = Math.max(target, x0 + (live.minW ?? MIN_BAND_WIDTH));
  return clampTowards(x1, wanted, x => validCrop({x0, x1: x, y0: live.top, y1: live.bottom}, live.angle, center, frame));
}
export function dragAngle(target, live, center, frame) {
  const x0 = currentX0(live), x1 = currentX1(live);
  return clampTowards(live.angle, target, a => validCrop({x0, x1, y0: live.top, y1: live.bottom}, a, center, frame));
}

// Build a clamped rectangle from two drag endpoints in rectified (or unrotated reference) coordinates.
export function draftBox(start, end, frame, angle = 0, center = [0, 0]) {
  if (!start || !end || !frame) return null;
  let x0 = Math.min(start[0], end[0]), x1 = Math.max(start[0], end[0]);
  let y0 = Math.min(start[1], end[1]), y1 = Math.max(start[1], end[1]);
  if (Math.abs(angle) < 1e-9) {
    x0 = Math.max(SLACK, Math.min(frame.width - SLACK, x0));
    x1 = Math.max(SLACK, Math.min(frame.width - SLACK, x1));
    y0 = Math.max(SLACK, Math.min(frame.height - SLACK, y0));
    y1 = Math.max(SLACK, Math.min(frame.height - SLACK, y1));
  } else if (!validCrop({x0, y0, x1, y1}, angle, center, frame)) {
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const seed = {x0: cx - 0.5, x1: cx + 0.5, y0: cy - 0.5, y1: cy + 0.5};
    if (!validCrop(seed, angle, center, frame)) return null;
    const scale = clampTowards(0, 1, t => validCrop({
      x0: cx + (x0 - cx) * t,
      x1: cx + (x1 - cx) * t,
      y0: cy + (y0 - cy) * t,
      y1: cy + (y1 - cy) * t,
    }, angle, center, frame));
    x0 = cx + (x0 - cx) * scale;
    x1 = cx + (x1 - cx) * scale;
    y0 = cy + (y0 - cy) * scale;
    y1 = cy + (y1 - cy) * scale;
  }
  return {
    rect: {x0, y0, x1, y1},
    valid: (x1 - x0) >= MIN_BAND_WIDTH && (y1 - y0) >= MIN_BAND_HEIGHT && validCrop({x0, y0, x1, y1}, angle, center, frame),
  };
}

// Smallest signed turn from one direction to another, in degrees.
export function turnBetween(from, to) {
  const delta = (to - from) * 180 / Math.PI;
  return ((delta + 540) % 360) - 180;
}

// View rectangle after zooming about a point, keeping that point under the pointer.
export function zoomedView(view, base, point, factor, maxZoom = 6) {
  const width = Math.min(base.width, Math.max(base.width / maxZoom, view.width / factor));
  if (width >= base.width - 1e-9) return null;
  const ratio = width / view.width;
  const height = base.height * width / base.width;
  return {x: point[0] - (point[0] - view.x) * ratio, y: point[1] - (point[1] - view.y) * ratio, width, height};
}
