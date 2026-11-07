import test from 'node:test';
import assert from 'node:assert/strict';
import {panelWidthFactors} from './laneScale.js';
const panel = (id, count, spans = []) => ({panel_id:id, display_geometry:{lane_layout:{lane_count:count, spans}}});
test('explicit three-lane membrane is one third the width of nine lanes by default', () => {
  const project = {sample_groups:[], figure_scale_mode:'equal_lane_width'};
  const panels = [panel('nine',9), panel('three',3), panel('unknown',null)];
  assert.deepEqual(panelWidthFactors(project, panels), {nine:1, three:1/3, unknown:1});
  assert.deepEqual(panelWidthFactors({...project, figure_scale_mode:'equal_width'}, panels), {nine:1, three:1/3, unknown:1});
});
test('physical gaps retain width without becoming lanes or implying sample identity', () => {
  const nine = Array.from({length:9}, (_, i) => [i < 3 ? i/10 : (i+1)/10, i < 3 ? (i+1)/10 : (i+2)/10]);
  const factors = panelWidthFactors({sample_groups:[], figure_scale_mode:'equal_lane_width'}, [panel('nine',9,nine), panel('three',3)]);
  assert.ok(Math.abs(factors.three - 0.3) < 1e-12);
});
