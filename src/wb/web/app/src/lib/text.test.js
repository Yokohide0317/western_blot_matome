import test from 'node:test';
import assert from 'node:assert/strict';
import { redPixelsForCapture } from './text.js';

test('crop saturation takes precedence over saturation elsewhere in a raw frame', () => {
  const capture = {capture_id: 'C', presentation_source_asset_id: 'S'};
  const panel = {region_id: 'R', selected_band_row_candidate_id: 'ROW'};
  const row = {candidate_id: 'ROW', red_pixels_in_crop_by_capture: {C: 0}};
  const project = {membrane_regions: [{region_id: 'R', band_row_candidates: [row]}],
    sources: [{source_asset_id: 'S', red_overlay_present: true}]};
  assert.equal(redPixelsForCapture(project, panel, capture), false);
  row.red_pixels_in_crop_by_capture.C = 5;
  assert.equal(redPixelsForCapture(project, panel, capture), true);
  delete row.red_pixels_in_crop_by_capture.C;
  assert.equal(redPixelsForCapture(project, panel, capture), true);
});
