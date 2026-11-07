// All points are image-edge coordinates. CSS and device pixels never enter saved geometry.
export const identity = () => [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

export function apply(matrix, [x, y]) {
  const w = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2];
  return [(matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / w,
    (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / w];
}

export function inverse(m) {
  const [[a, b, x], [c, d, y]] = m;
  const det = a * d - b * c;
  if (Math.abs(det) < 1e-12) throw new Error('逆変換できない行列です');
  return [[d / det, -b / det, (b * y - d * x) / det],
    [-c / det, a / det, (c * x - a * y) / det], [0, 0, 1]];
}

export function rotation(degrees, [x, y]) {
  const angle = degrees * Math.PI / 180;
  const c = Math.cos(angle), s = Math.sin(angle);
  return [[c, -s, x - c * x + s * y], [s, c, y - s * x - c * y], [0, 0, 1]];
}

export function cropPolygon(rect, referenceToRectified, sourceToReference = identity()) {
  const undoRotation = inverse(referenceToRectified);
  const undoAlignment = inverse(sourceToReference);
  return [[rect.x0, rect.y0], [rect.x1, rect.y0], [rect.x1, rect.y1], [rect.x0, rect.y1]]
    .map(point => apply(undoAlignment, apply(undoRotation, point)));
}

export const svgMatrix = m => `matrix(${m[0][0]} ${m[1][0]} ${m[0][1]} ${m[1][1]} ${m[0][2]} ${m[1][2]})`;
export const pointsAttribute = points => points.map(point => point.join(',')).join(' ');

export function rotationSettings(matrix, evidence, frame) {
  const degrees = Math.atan2(matrix[1][0], matrix[0][0]) * 180 / Math.PI;
  if (Array.isArray(evidence?.center)) return { degrees, center: evidence.center };
  // Solve (I - R) center = translation for older transforms without a recorded center.
  const a = 1 - matrix[0][0], b = -matrix[0][1];
  const c = -matrix[1][0], d = 1 - matrix[1][1];
  const det = a * d - b * c;
  const center = Math.abs(det) < 1e-12 ? [frame.width / 2, frame.height / 2]
    : [(d * matrix[0][2] - b * matrix[1][2]) / det,
      (a * matrix[1][2] - c * matrix[0][2]) / det];
  return { degrees, center };
}
