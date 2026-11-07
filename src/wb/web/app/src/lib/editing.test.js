import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dragTop, dragBottom, dragLeft, dragRight, dragAngle, draftBox, turnBetween, zoomedView, validCrop } from './editing.js';

const frame = {width: 100, height: 80};
const center = [50, 40];
const live = {rect: {x0: 20, x1: 80}, left: 20, right: 80, top: 30, bottom: 50, angle: 2.5};

test('top and bottom handles keep a minimum band and stay inside the frame', () => {
  assert.equal(dragTop(48, live, center, frame), 44);
  assert.equal(dragBottom(31, live, center, frame), 36);
  const top = dragTop(-500, live, center, frame);
  assert.ok(top > -5 && validCrop({...live.rect, y0: top, y1: live.bottom}, live.angle, center, frame));
  const bottom = dragBottom(900, live, center, frame);
  assert.ok(bottom < 90 && validCrop({...live.rect, y0: live.top, y1: bottom}, live.angle, center, frame));
  assert.equal(dragTop(33.3333, live, center, frame), 33.3333);
});
test('left and right handles keep a minimum width and stay inside the frame', () => {
  assert.equal(dragLeft(78, live, center, frame), 74);
  assert.equal(dragRight(22, live, center, frame), 26);
  const left = dragLeft(-200, live, center, frame);
  assert.ok(left > 0 && validCrop({x0: left, x1: live.right, y0: live.top, y1: live.bottom}, live.angle, center, frame));
  const right = dragRight(400, live, center, frame);
  assert.ok(right < 100 && validCrop({x0: live.left, x1: right, y0: live.top, y1: live.bottom}, live.angle, center, frame));
});
test('a band thinner than the minimum can move but not shrink', () => {
  const thin = {...live, top: 30, bottom: 32, min: 2};
  assert.equal(dragTop(31, thin, center, frame), 30);
  assert.equal(dragTop(29.25, thin, center, frame), 29.25);
});
test('rotation stops before a corner leaves the frame', () => {
  const wide = {...live, rect: {x0: 3, x1: 97}, left: 3, right: 97};
  const angle = dragAngle(60, wide, center, frame);
  assert.ok(angle < 60 && angle > 2.5 && validCrop({...wide.rect, y0: wide.top, y1: wide.bottom}, angle, center, frame));
  assert.equal(dragAngle(3, live, center, frame), 3);
});
test('draftBox normalizes drag direction, enforces minimum size, and clamps rotated boxes inside frame', () => {
  const small = draftBox([12, 15], [15, 18], frame);
  assert.equal(small.valid, false);
  const box = draftBox([85, 65], [15, 20], frame);
  assert.equal(box.valid, true);
  assert.deepEqual(box.rect, {x0: 15, y0: 20, x1: 85, y1: 65});
  const rotated = draftBox([-10, 20], [95, 60], frame, 15, center);
  assert.ok(rotated && rotated.valid);
  assert.ok(validCrop(rotated.rect, 15, center, frame));
});
test('turns take the short way round', () => {
  assert.ok(Math.abs(turnBetween(3.1, -3.1) - 2 * (Math.PI - 3.1) * 180 / Math.PI) < 1e-9);
  assert.ok(Math.abs(turnBetween(0, Math.PI / 2) - 90) < 1e-9);
});
test('zoom keeps the pointer fixed, is capped at 6x and returns to the base', () => {
  const base = {x: 0, y: 0, width: 100, height: 50};
  const view = zoomedView(base, base, [25, 10], 2);
  assert.equal(view.width, 50);
  assert.equal(view.x, 12.5);
  assert.equal(view.y, 5);
  assert.ok(Math.abs(zoomedView(view, base, [0, 0], 1000).width - 100 / 6) < 1e-9);
  assert.equal(zoomedView(view, base, [0, 0], 0.1), null);
});
