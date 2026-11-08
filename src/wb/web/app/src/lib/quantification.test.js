import test from 'node:test';
import assert from 'node:assert/strict';
import { clampPosition, moveRoi, setRoi, resizedLanes, overlapIssues, arrowDelta, sameLanes, statusLabel, statusMark, formatNumber, groupTitle, laneName } from './quantification.js';

const image = {width: 100, height: 60};
const size = {width: 20, height: 10};
const lane = {lane_id: 1, x: 10, y: 10, background_x: 10, background_y: 30, excluded: false};

test('boxes stay fully inside the image and keep their size', () => {
  assert.deepEqual(clampPosition(-5, 70, 20, 10, image), [0, 50]);
  assert.deepEqual(clampPosition(95.4, 3.6, 20, 10, image), [80, 4]);
  assert.deepEqual(moveRoi(lane, 'band', -50, 0, size, image), {...lane, x: 0});
  assert.deepEqual(moveRoi(lane, 'background', 0, 100, size, image), {...lane, background_y: 50});
  assert.deepEqual(setRoi(lane, 'background', 200, 200, size, image), {...lane, background_x: 80, background_y: 50});
});

test('arrow keys move by one pixel, ten with Shift', () => {
  assert.deepEqual(arrowDelta({key: 'ArrowLeft'}), [-1, 0]);
  assert.deepEqual(arrowDelta({key: 'ArrowDown', shiftKey: true}), [0, 10]);
  assert.equal(arrowDelta({key: 'a'}), null);
});

test('resizing keeps every centre and rejects a box that would leave the image', () => {
  const lanes = [lane, {...lane, lane_id: 2, x: 50}];
  const grown = resizedLanes(lanes, size, {width: 30, height: 14}, image);
  assert.deepEqual(grown.map(l => [l.x, l.y, l.background_x, l.background_y]), [[5, 8, 5, 28], [45, 8, 5, 28]]);
  assert.equal(resizedLanes([{...lane, x: 0}], size, {width: 30, height: 10}, image), null);
  assert.equal(resizedLanes(lanes, size, {width: 101, height: 10}, image), null);
  assert.equal(resizedLanes(lanes, size, {width: 0, height: 10}, image), null);
});

test('overlaps are reported for the band with its background and with other bands', () => {
  const lanes = [lane, {...lane, lane_id: 2, x: 25, y: 12, background_y: 45}, {...lane, lane_id: 3, x: 80, background_y: 10, excluded: true}];
  assert.deepEqual(overlapIssues(lanes, size), {
    1: ['レーン 2 のバンドと重なっています'], 2: ['レーン 1 のバンドと重なっています'],
  });
  assert.deepEqual(Object.keys(overlapIssues([{...lane, background_y: 12}], size)), ['1']);
});

test('lane lists compare by position, not identity', () => {
  assert.equal(sameLanes([lane], [{...lane}]), true);
  assert.equal(sameLanes([lane], [{...lane, x: 11}]), false);
  assert.equal(sameLanes([lane], []), false);
});

test('negative and saturated results keep a non-colour label', () => {
  assert.equal(statusLabel({status: 'ok', net_signal: -3, saturated_pixels: 0}), '背景以下');
  assert.equal(statusLabel({status: 'saturated', net_signal: null, saturated_pixels: 4}), '飽和（値なし）');
  assert.equal(statusLabel({status: 'ok', net_signal: 5, saturated_pixels: 0}), '有効');
  assert.equal(statusMark({net_signal: null}), '✕');
  assert.equal(statusMark({net_signal: -1, saturated_pixels: 0}), '▲');
  assert.equal(statusMark({net_signal: 1, saturated_pixels: 0}), '✓');
});

test('numbers keep negatives and show missing values as a dash', () => {
  assert.equal(formatNumber(-12.345, 1), '-12.3');
  assert.equal(formatNumber(1234567), '1234567');
  assert.equal(formatNumber(null), '—');
});

test('titles and sample names come from the project', () => {
  const project = {panels: [{panel_id: 'p1', label: 'Protein A', sample_group_id: 's'}], captures: [{capture_id: 'c1', exposure_s: 30}],
    sample_groups: [{sample_group_id: 's', samples: [{name: 'Sample 1'}, {name: null}]}]};
  const group = {group_id: 'g', panel_id: 'p1', capture_id: 'c1', results: null};
  assert.equal(groupTitle(group, project, 1), 'Protein A · 30s');
  assert.equal(groupTitle(group, project, 2), 'Protein A · 30s · 2');
  assert.equal(laneName(group, project, 1, 0), 'Sample 1');
  assert.equal(laneName(group, project, 2, 1), '');
  assert.equal(laneName({...group, results: {rows: [{lane_id: 2, sample_name: 'Sample 9'}]}}, project, 2, 1), 'Sample 9');
});

test('mouse handles resize every band and background about its own adjusted centre', async () => {
  const {resizeFromDrag} = await import('./quantification.js');
  const lanes = [lane, {...lane, lane_id:2, x:50,y:13,background_x:55,background_y:40}];
  const centers = (rows,s) => rows.map(l=>[l.x+s.width/2,l.y+s.height/2,l.background_x+s.width/2,l.background_y+s.height/2]);
  const target = resizeFromDrag(lanes,size,image,1,1,3,2);
  assert.deepEqual(target.size,{width:26,height:14});
  assert.deepEqual(centers(target.lanes,target.size),centers(lanes,size));
  const restored = resizeFromDrag(target.lanes,target.size,image,-1,-1,3,2);
  assert.deepEqual(restored.lanes,lanes);
  assert.deepEqual(restored.size,size);
  const limited = resizeFromDrag(lanes,size,image,1,1,999,999);
  assert.deepEqual(centers(limited.lanes,limited.size),centers(lanes,size));
  assert(limited.lanes.every(l=>l.x>=0&&l.y>=0&&l.background_x>=0&&l.background_y>=0));
  assert.equal(resizedLanes(lanes,size,{width:21,height:10},image),null);
});
