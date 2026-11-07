import { test } from 'node:test';
import assert from 'node:assert/strict';
import { apply, inverse, rotation, cropPolygon, rotationSettings, svgMatrix } from './coordinates.js';
const near = (a, b) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 1e-9, `${a} != ${b}`));

test('clockwise rotation preserves fractional edge coordinates and round-trips', () => {
  const matrix = rotation(13.2, [52.5, 30.25]);
  near(apply(inverse(matrix), apply(matrix, [0.5, 0.5])), [0.5, 0.5]);
  near(apply(rotation(90, [0, 0]), [1, 0]), [0, 1]);
});
test('crop is an oblique four-corner polygon, undoing rotation then alignment', () => {
  const polygon = cropPolygon({x0: 10.25, y0: 20.5, x1: 60.25, y1: 30.5}, rotation(30, [35, 25]), [[1,0,5],[0,1,3],[0,0,1]]);
  polygon.forEach((p, i) => near(apply(rotation(30, [35,25]), [p[0]+5,p[1]+3]), [[10.25,20.5],[60.25,20.5],[60.25,30.5],[10.25,30.5]][i]));
  assert.notEqual(polygon[0][1], polygon[1][1]);
});
test('rotation center is recovered without rounding or a CSS scale', () => {
  const m = rotation(-1.75, [1030.2, 650.7]);
  near(rotationSettings(m, null, {width: 2000, height: 1000}).center, [1030.2, 650.7]);
  near(rotationSettings(m, {center: [3, 4]}, {}).center, [3,4]);
  assert.equal(svgMatrix([[1,2,3],[4,5,6],[0,0,1]]), 'matrix(1 4 2 5 3 6)');
});
test('singular affine is rejected', () => assert.throws(() => inverse([[0,0,0],[0,0,0],[0,0,1]])));
