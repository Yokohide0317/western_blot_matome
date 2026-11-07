// Red saturation in the chosen band (or a red overlay baked into the export) marks an exposure as unusable for the figure.
export function redPixelsForCapture(project, panel, capture) {
  const region = project.membrane_regions.find(r => r.region_id === panel.region_id);
  const candidate = region?.band_row_candidates?.find(c => c.candidate_id === panel.selected_band_row_candidate_id);
  const source = project.sources.find(s => s.source_asset_id === capture.presentation_source_asset_id);
  const count = candidate?.red_pixels_in_crop_by_capture?.[capture.capture_id];
  return count == null ? source?.red_overlay_present === true : count > 0;
}
