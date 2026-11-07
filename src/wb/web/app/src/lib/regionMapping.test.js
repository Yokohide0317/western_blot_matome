import test from 'node:test';
import assert from 'node:assert/strict';
import {identity} from './coordinates.js';
import {regionDisplayPoints} from './regionMapping.js';
const translation = (x, y) => [[1,0,x],[0,1,y],[0,0,1]];
const first = {transform_id:'t1', kind:'marker_to_reference', from_frame_id:'color', to_frame_id:'ref1', matrix:translation(5,10)};
const second = {transform_id:'t2', kind:'marker_to_reference', from_frame_id:'color', to_frame_id:'ref2', matrix:translation(-3,7)};
const project = {marker_registrations:[], transforms:[first, second]};
const region = {region_id:'m2', geometry:{frame_id:'ref2', polygon:[[17,37],[27,37],[27,47],[17,47]]}};
const view = {mode:'marker', canMap:true, displayFrameId:'color', referenceFrameId:'ref1', toDisplay:identity(), markerTransform:first};
test('each membrane uses its own registration when shown on Color', () => {
  assert.deepEqual(regionDisplayPoints(project, region, view), [[20,30],[30,30],[30,40],[20,40]]);
  assert.deepEqual(regionDisplayPoints(project, region, {...view, canMap:false}), [[20,30],[30,30],[30,40],[20,40]]);
});
test('different references bridge through the same registered Color frame', () => {
  assert.deepEqual(regionDisplayPoints(project, region, {...view, mode:'lum',displayFrameId:'exposure',toDisplay:translation(-2,1)}), [[23,41],[33,41],[33,51],[23,51]]);
});
test('missing registration and unrelated Color frames never imply identity', () => {
  const missing = {...project, transforms:[first]};
  assert.equal(regionDisplayPoints(missing, region, view), null);
  assert.equal(regionDisplayPoints(missing, region, {...view,mode:'lum'}), null);
  const unrelated = {...project, transforms:[first,{...second, from_frame_id:'other-color'}]};
  assert.equal(regionDisplayPoints(unrelated, region, view), null);
  assert.equal(regionDisplayPoints(unrelated, region, {...view,mode:'lum'}), null);
});

test('Selection crops undo rotation then use their own Color registration', async () => {
  const {selectionDisplayPoints} = await import('./regionMapping.js');
  const rotation = {transform_id:'rotate', matrix:[[0,-1,0],[1,0,0],[0,0,1]]};
  const model = {...project, membrane_regions:[region], transforms:[...project.transforms,rotation]};
  const panel = {region_id:'m2', display_geometry:{reference_frame_id:'ref2', reference_to_rectified_transform_id:'rotate', crop_rect:{x0:-47,y0:17,x1:-37,y1:27}}};
  assert.deepEqual(selectionDisplayPoints(model,panel,view),[[20,40],[20,30],[30,30],[30,40]]);
  assert.equal(selectionDisplayPoints({...model,transforms:[rotation,first]},panel,view),null);
});
