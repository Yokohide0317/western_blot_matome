import test from 'node:test';
import assert from 'node:assert/strict';
import { markerXSpan, effectiveMarkerRect, dragMarker } from './markers.js';

const crop = {x0: 100, y0: 50, x1: 300, y1: 80};

test('x span is taken where a slanted marker crosses the crop height', () => {
  const slanted = [[0, 0], [10, 0], [110, 100], [100, 100]];
  assert.deepEqual(markerXSpan(slanted, 40, 60), [40, 70]);
});

test('a saved x range wins, and the height is always the crop\'s', () => {
  const panel = {display_geometry: {crop_rect: crop, marker_x: {x0: 1, x1: 2}}};
  assert.deepEqual(effectiveMarkerRect({marker_registrations: [], transforms: []}, panel), {x0: 1, x1: 2, y0: 50, y1: 80});
});

test('dragging trims or moves sideways only', () => {
  const start = {x0: 10, x1: 30, y0: 50, y1: 80};
  assert.equal(dragMarker('left', start, 29).x0, 26);
  assert.equal(dragMarker('right', start, 12).x1, 14);
  assert.deepEqual(dragMarker('move', start, 5), {x0: 15, x1: 35, y0: 50, y1: 80});
  assert.deepEqual(dragMarker('top', start, 0), start);
});
