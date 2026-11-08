// Quantification ROIs live in full-resolution raw pixels: integer top-left corners and one width/height per group.
// The server owns validation; these helpers only keep the UI inside the image and describe what the server saved.

export const KEY_STEP = 1;
export const KEY_STEP_FAST = 10;
export const MIN_ROI = 1;

const clamp = (value, low, high) => Math.min(Math.max(value, low), Math.max(low, high));

export const toInt = value => {
  const number = Math.round(Number(value));
  return Number.isFinite(number) ? number : null;
};

// Keep a width x height box fully inside the image. Boxes are never resized or cut.
export function clampPosition(x, y, width, height, image) {
  return [clamp(Math.round(x), 0, image.width - width), clamp(Math.round(y), 0, image.height - height)];
}

export function moveRoi(lane, kind, dx, dy, size, image) {
  const keyX = kind === 'background' ? 'background_x' : 'x', keyY = kind === 'background' ? 'background_y' : 'y';
  const [x, y] = clampPosition(lane[keyX] + dx, lane[keyY] + dy, size.width, size.height, image);
  return {...lane, [keyX]: x, [keyY]: y};
}

export function setRoi(lane, kind, x, y, size, image) {
  const keyX = kind === 'background' ? 'background_x' : 'x', keyY = kind === 'background' ? 'background_y' : 'y';
  const [nx, ny] = clampPosition(x, y, size.width, size.height, image);
  return {...lane, [keyX]: nx, [keyY]: ny};
}

export function updateLane(lanes, laneId, change) {
  return lanes.map(lane => lane.lane_id === laneId ? change(lane) : lane);
}

export function sameLanes(a, b) {
  return a.length === b.length && a.every((lane, i) => ['lane_id', 'x', 'y', 'background_x', 'background_y', 'excluded'].every(key => lane[key] === b[i][key]));
}

export function arrowDelta(event) {
  const step = event.shiftKey ? KEY_STEP_FAST : KEY_STEP;
  switch (event.key) {
    case 'ArrowLeft': return [-step, 0];
    case 'ArrowRight': return [step, 0];
    case 'ArrowUp': return [0, -step];
    case 'ArrowDown': return [0, step];
    default: return null;
  }
}

// Common-size changes keep every box centre. Returns null when any box would leave the image.
export function resizedLanes(lanes, from, to, image) {
  if ((to.width - from.width) % 2 || (to.height - from.height) % 2) return null;
  if (!(to.width >= MIN_ROI && to.height >= MIN_ROI) || to.width > image.width || to.height > image.height) return null;
  const next = [];
  for (const lane of lanes) {
    const placed = {...lane};
    for (const [keyX, keyY] of [['x', 'y'], ['background_x', 'background_y']]) {
      const x = lane[keyX] + Math.floor((from.width - to.width) / 2), y = lane[keyY] + Math.floor((from.height - to.height) / 2);
      if (x < 0 || y < 0 || x + to.width > image.width || y + to.height > image.height) return null;
      placed[keyX] = x; placed[keyY] = y;
    }
    next.push(placed);
  }
  return next;
}

export function boxesOverlap(ax, ay, bx, by, size) {
  return ax < bx + size.width && bx < ax + size.width && ay < by + size.height && by < ay + size.height;
}
export const bandOverlapsBackground = (lane, size) => boxesOverlap(lane.x, lane.y, lane.background_x, lane.background_y, size);

export function overlapIssues(lanes, size) {
  const issues = {};
  for (const lane of lanes) {
    if (lane.excluded) continue;
    const notes = [];
    if (bandOverlapsBackground(lane, size)) notes.push('背景とバンドが重なっています');
    for (const other of lanes) {
      if (other.lane_id === lane.lane_id || other.excluded) continue;
      if (boxesOverlap(lane.x, lane.y, other.x, other.y, size)) notes.push(`レーン ${other.lane_id} のバンドと重なっています`);
    }
    if (notes.length) issues[lane.lane_id] = notes;
  }
  return issues;
}

const statusNames = {
  ok: '有効', valid: '有効', computed: '有効', ready: '有効',
  below_background: '背景以下', negative: '背景以下',
  saturated: '飽和', invalid: '無効', excluded: '除外', stale: '再計算が必要',
};
export function statusLabel(row) {
  const named = statusNames[String(row.status ?? '').toLowerCase()];
  if (row.net_signal == null) return row.saturated_pixels > 0 ? '飽和（値なし）' : (named && named !== '有効' ? named : row.status || '値なし');
  if (row.net_signal < 0) return '背景以下';
  return row.saturated_pixels > 0 ? '飽和あり' : (named || row.status || '有効');
}
// A mark that does not depend on colour: ✓ usable, ▲ usable but needs a look, ✕ no value.
export function statusMark(row) {
  if (!row || row.net_signal == null) return '✕';
  if (row.net_signal < 0 || row.saturated_pixels > 0) return '▲';
  return '✓';
}

export function formatNumber(value, digits = 0) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false});
}

export const polarityNames = {high: '高値が強い信号（明るいバンド）', low: '低値が強い信号（暗いバンド）'};

export function laneName(group, project, laneId, index) {
  const row = group?.results?.rows?.find(r => r.lane_id === laneId);
  if (row?.sample_name) return row.sample_name;
  return sampleNameFor(project, group, index);
}
function sampleNameFor(project, group, index) {
  const panel = project?.panels?.find(p => p.panel_id === group?.panel_id);
  const samples = project?.sample_groups?.find(g => g.sample_group_id === panel?.sample_group_id)?.samples;
  return samples?.[index]?.name || '';
}

export function groupTitle(group, project, number) {
  const panel = project.panels.find(p => p.panel_id === group.panel_id);
  const capture = project.captures.find(c => c.capture_id === group.capture_id);
  const exposure = capture?.exposure_s != null ? `${Number(capture.exposure_s)}s` : group.capture_id;
  return `${panel?.label || group.panel_id} · ${exposure}${number > 1 ? ` · ${number}` : ''}`;
}

// Symmetric resizing changes each edge by whole pixels, preserving every ROI centre exactly.
export function resizeFromDrag(lanes, from, image, horizontal, vertical, dx, dy) {
  const limit = (axis, old, delta, direction) => {
    if (!direction) return old;
    const total = axis === 'x' ? image.width : image.height;
    const centers = lanes.flatMap(l => [l[axis] + old / 2, l[`background_${axis}`] + old / 2]);
    const max = Math.min(...centers.map(c => 2 * Math.min(c, total - c)));
    const min = old % 2 || 2;
    const upper = old + 2 * Math.floor((max - old) / 2);
    return Math.max(min, Math.min(upper, old + 2 * Math.round(direction * delta)));
  };
  const size = {width: limit('x', from.width, dx, horizontal), height: limit('y', from.height, dy, vertical)};
  return {size, lanes: resizedLanes(lanes, from, size, image)};
}
