import {apply, inverse, cropPolygon} from './coordinates.js';

function markerRegistration(project, region, colorFrameId) {
  const preferred = project.marker_registrations
    .filter(registration => registration.membrane_region_id === region.region_id)
    .map(registration => registration.transform_id);
  return project.transforms
    .filter(transform => transform.kind === 'marker_to_reference'
      && transform.to_frame_id === region.geometry.frame_id
      && transform.from_frame_id === colorFrameId)
    .sort((a, b) => Number(preferred.includes(b.transform_id)) - Number(preferred.includes(a.transform_id)))[0];
}

export function regionDisplayPoints(project, region, view) {
  if (!region.geometry) return null;
  const {frame_id: source, polygon} = region.geometry;
  if (source === view.displayFrameId) return polygon;
  if (view.mode === 'marker') {
    const marker = markerRegistration(project, region, view.displayFrameId);
    return marker ? polygon.map(point => apply(inverse(marker.matrix), point)) : null;
  }
  if (!view.canMap) return null;
  if (source === view.referenceFrameId) return polygon.map(point => apply(view.toDisplay, point));
  // Bridge two independently registered reference frames through their actual
  // common Color frame. An absent registration never means an identity map.
  const target = view.markerTransform;
  if (target?.kind !== 'marker_to_reference' || target.to_frame_id !== view.referenceFrameId) return null;
  const marker = markerRegistration(project, region, target.from_frame_id);
  return marker ? polygon.map(point => apply(view.toDisplay,
    apply(target.matrix, apply(inverse(marker.matrix), point)))) : null;
}

export function selectionDisplayPoints(project, panel, view) {
  const geometry = panel.display_geometry;
  const region = project.membrane_regions.find(r => r.region_id === panel.region_id);
  const transform = project.transforms.find(t => t.transform_id === geometry?.reference_to_rectified_transform_id);
  if (!region || !geometry?.crop_rect || !transform) return null;
  return regionDisplayPoints(project, {...region, geometry: {
    frame_id: geometry.reference_frame_id,
    polygon: cropPolygon(geometry.crop_rect, transform.matrix),
  }}, view);
}
