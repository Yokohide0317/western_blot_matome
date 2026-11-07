// Match layout.compute_layout: lane counts are user assignments, never peak counts.
export function panelWidthFactors(project, panels) {
  const units = panels.map(panel => {
    const layout = panel.display_geometry?.lane_layout;
    const group = project.sample_groups.find(g => g.sample_group_id === panel.sample_group_id);
    const count = layout?.lane_count ?? group?.lane_count;
    const occupied = layout?.spans?.length
      ? layout.spans.reduce((sum, [start, end]) => sum + end - start, 0) : 1;
    return count ? count / occupied : null;
  });
  const largest = Math.max(1, ...units.filter(value => value !== null));
  return Object.fromEntries(panels.map((panel, i) => [panel.panel_id,
    (units[i] ?? largest) / largest]));
}
