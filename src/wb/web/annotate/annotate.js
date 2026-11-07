'use strict';

// Local annotation screen. Coordinates are frame edge coordinates; the viewer
// (common/frameview.js) converts screen positions and nothing is rounded.
// Zoom, pan and the draft below are screen state and never reach project.json
// until Save sends them in one save_panel_edit request.

const $ = (id) => document.getElementById(id);

const state = {
  project: null,
  panel: null,
  region: null,
  // Unsaved edits for the selected panel. Reset whenever a panel is (re)selected.
  annotation_draft: null,
  dirty: false,
  markerFit: null,
  proposal: null,
  viewport: WbFrameView.identityViewport(),
};

const view = WbFrameView.create({
  id: 'editor',
  viewportId: 'viewport',
  imageId: 'source',
  overlaysId: 'overlays',
});
$('editor-host').append(view.svg);

window.wbAnnotate = { state };

// ---- server ----

async function request(path, body) {
  const options = body === undefined
    ? {}
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (!response.ok) {
    const error = await response.json();
    throw Error(typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail));
  }
  return response.json();
}

async function mutate(operation, data) {
  state.project = await request('/api/' + operation, {
    ...data,
    expected_revision: state.project.project_revision,
  });
  return state.project;
}

// Runs an action; any failure goes to the message line.
function guarded(action) {
  return async () => {
    try {
      await action();
    } catch (error) {
      showMessage(error);
    }
  };
}

function showMessage(message) {
  $('message').textContent = message instanceof Error ? message.message : message;
}

// ---- lookups ----

function frameById(frameId) {
  return state.project.frames.find((f) => f.frame_id === frameId);
}

function captureFrame(captureId) {
  const capture = state.project.captures.find((c) => c.capture_id === captureId);
  return state.project.frames.find((f) => f.source_asset_id === capture?.presentation_source_asset_id);
}

function imageSet(imageSetId) {
  return state.project.image_sets.find((s) => s.image_set_id === imageSetId);
}

function regionOf(panel) {
  return state.project.membrane_regions.find((r) => r.region_id === panel.region_id);
}

function sampleGroup(groupId) {
  return state.project.sample_groups.find((g) => g.sample_group_id === groupId);
}

function numberOrNull(inputId) {
  const value = $(inputId).value;
  return value === '' ? null : Number(value);
}

function rotationRad() {
  return Number($('rotation').value) * Math.PI / 180;
}

// The frame the editor shows: the marker image for marker tools, else the reference exposure.
function currentFrameId() {
  const tool = $('tool').value;
  const onMarker = tool === 'marker_roi' || (tool === 'marker' && $('landmark-side').value === 'marker');
  return onMarker ? $('marker-frame').value : $('reference').value;
}

// ---- draft ----

function newDraft(panel, region) {
  const layout = panel.display_geometry?.lane_layout;
  return {
    polygon: region.geometry?.polygon?.map((p) => [...p]) || [],
    points: [],
    known: {},
    centers: layout?.centers_x || [],
    annotations: [],
    markerPoints: [],
    referencePoints: [],
    markerRoi: [],
  };
}

function markDirty() {
  state.dirty = true;
  drawEditor();
}

// ---- form helpers ----

function fillOptions(selectId, items, selected, labelOf) {
  const select = $(selectId);
  select.replaceChildren(new Option('選択してください', ''));
  for (const item of items) select.add(new Option(labelOf(item), item.id));
  select.value = selected || '';
}

function referenceOptions(imageSetId) {
  const captureIds = imageSet(imageSetId)?.capture_ids || [];
  return captureIds.map((captureId) => {
    const capture = state.project.captures.find((c) => c.capture_id === captureId);
    return { id: captureFrame(captureId)?.frame_id, exposure: capture.exposure_s };
  });
}

function fillReferenceOptions(imageSetId, selected) {
  fillOptions('reference', referenceOptions(imageSetId), selected, (x) => `${x.exposure} 秒 (${x.id})`);
}

function sampleNames(groupId) {
  return (sampleGroup(groupId)?.samples || []).map((s) => s.name || '').join('\n');
}

function regionStatusText(region) {
  if (region.region_assignment_status === 'confirmed') return '確定';
  if (region.region_assignment_status === 'proposed') return '提案';
  return region.image_set_id || region.geometry ? '設定中' : '未割当';
}

// ---- rendering ----

function selectPanel(panelId) {
  state.proposal = null;
  $('proposal-frame-control').hidden = true; $('proposal-group-control').hidden = true;
  $('tool').value = 'region';
  state.panel = state.project.panels.find((p) => p.panel_id === panelId);
  state.region = regionOf(state.panel);
  state.annotation_draft = newDraft(state.panel, state.region);
  state.dirty = false;
  state.markerFit = null;
  renderAll();
}

function renderAll() {
  renderRegionList();
  renderSeriesControls();
  renderSampleControls();
  renderMarkerFrames();
  renderGeometryInputs();
  renderBandRowCandidates();
  drawEditor();
  renderExposures();
  renderAlignmentCheck();
}


function renderRegionList() {
  $('regions').replaceChildren();
  for (const panel of state.project.panels) {
    const region = regionOf(panel);
    const button = document.createElement('button');
    button.className = 'region' + (panel.panel_id === state.panel?.panel_id ? ' active' : '');
    const status = panel.review_status === 'confirmed' ? '確定'
      : panel.review_status === 'proposed' ? '提案' : regionStatusText(region);
    button.textContent = `${panel.label || '未設定'}（${region.user_label}）— ${status}`;
    button.onclick = () => selectPanel(panel.panel_id);
    $('regions').append(button);
  }
}

function renderSeriesControls() {
  const sets = state.project.image_sets.map((s) => ({ id: s.image_set_id }));
  fillOptions('series', sets, state.panel.image_set_id || state.region.image_set_id, (x) => x.id);
  fillReferenceOptions($('series').value, state.region.reference_frame_id);
  if (state.region.reference_frame_id && !$('reference').value) {
    $('reference').add(new Option(state.region.reference_frame_id, state.region.reference_frame_id));
    $('reference').value = state.region.reference_frame_id;
  }
  $('assignment').value = state.region.region_assignment_status;
}

function renderSampleControls() {
  const groups = state.project.sample_groups.map((g) => ({ id: g.sample_group_id }));
  fillOptions('sample-group', groups, state.panel.sample_group_id, (x) => x.id);
  $('samples').value = sampleNames(state.panel.sample_group_id);

  $('control-for').replaceChildren();
  for (const other of state.project.panels) {
    if (other.panel_id === state.panel.panel_id) continue;
    const option = new Option(other.panel_id, other.panel_id);
    option.selected = state.panel.control_for_panel_ids.includes(other.panel_id);
    $('control-for').add(option);
  }
}

function renderMarkerFrames() {
  const frames = state.project.captures
    .filter((c) => c.modality === 'colorimetric')
    .map((c) => captureFrame(c.capture_id))
    .filter(Boolean);
  fillOptions('marker-frame', frames.map((f) => ({ id: f.frame_id })), $('marker-frame').value, (x) => x.id);
}

function renderGeometryInputs() {
  const geometry = state.panel.display_geometry;
  $('lane-count').value = geometry?.lane_layout?.lane_count ?? state.region.lane_count ?? '';
  $('crop-left').value = geometry?.crop_rect?.x0 ?? '';
  $('crop-right').value = geometry?.crop_rect?.x1 ?? '';
  $('crop-top').value = geometry?.crop_rect?.y0 ?? '';
  $('crop-bottom').value = geometry?.crop_rect?.y1 ?? '';
  $('width').value = geometry?.target_width_px ?? '';
  const rotation = state.project.transforms.find((t) => t.transform_id === geometry?.reference_to_rectified_transform_id);
  $('rotation').value = rotation ? Math.atan2(rotation.matrix[1][0], rotation.matrix[0][0]) * 180 / Math.PI : 0;
}

// The crop rectangle the draft lane centers and crop inputs describe, in the
// rotated (rectified) frame, or null while any part is missing.
function draftCropRect() {
  const { centers } = state.annotation_draft;
  const top = $('crop-top').value;
  const bottom = $('crop-bottom').value;
  if (top === '' || bottom === '') return null;
  if ($('crop-left').value !== '' && $('crop-right').value !== '') {
    return { x0: Number($('crop-left').value), x1: Number($('crop-right').value),
      y0: Number(top), y1: Number(bottom) };
  }
  if (centers.length < 2) return null;
  const pitch = (centers.at(-1) - centers[0]) / (centers.length - 1);
  return { x0: centers[0] - pitch / 2, x1: centers.at(-1) + pitch / 2, y0: Number(top), y1: Number(bottom) };
}

// Rotates a rectified-frame point back about the frame centre.
function unrotate(point, frame) {
  const angle = -rotationRad();
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const transform = state.project.transforms.find((t) => t.transform_id === state.panel.display_geometry?.reference_to_rectified_transform_id);
  const [cx, cy] = transform?.evidence?.center || [frame.width / 2, frame.height / 2];
  const [x, y] = point;
  return [cx + c * (x - cx) - s * (y - cy), cy + s * (x - cx) + c * (y - cy)];
}

function drawEditor() {
  if (state.proposal) { drawProposal(); return; }
  const frame = frameById(currentFrameId());
  if (!frame) {
    view.clearFrame();
    return;
  }
  view.setFrame({ frameId: frame.frame_id, width: frame.width, height: frame.height, href: `/api/image/frame/${frame.frame_id}.png` });
  state.viewport = { k: Number($('zoom').value) || 1, tx: Number($('pan-x').value) || 0, ty: Number($('pan-y').value) || 0 };
  view.applyViewport(state.viewport);

  const source = state.project.sources.find((s) => s.source_asset_id === frame.source_asset_id);
  $('badge').textContent = source?.red_overlay_present ? '赤表示あり' : '';

  view.clearOverlays();
  if (frame.frame_id === $('reference').value) drawReferenceOverlays(frame);
  else drawMarkerOverlays();
  $('draft-status').textContent = state.dirty ? 'annotation_draft: 未保存' : '未保存の編集なし';
}

function drawPath(points, className) {
  if (!points.length) return;
  const draw = points.length >= 3 ? view.addPolygon : view.addPolyline;
  draw(points, { class: className });
}

function drawPoint(point, className) {
  view.addPoint(point, { class: className });
}

function drawReferenceOverlays(frame) {
  const draft = state.annotation_draft;
  drawPath(draft.polygon, state.dirty ? 'draft' : '');
  drawBandRowCandidates(frame);
  for (const point of draft.points) drawPoint(point, 'draft');

  const saved = state.project.annotations.filter((a) => a.panel_id === state.panel.panel_id && a.frame_id === frame.frame_id);
  for (const annotation of [...saved, ...draft.annotations]) {
    const className = annotation.review_status === 'draft' ? 'draft' : '';
    if (annotation.geometry.type === 'point') drawPoint(annotation.geometry.coordinates[0], className);
    else drawPath(annotation.geometry.coordinates, className);
  }

  const laneY = Number($('crop-top').value) || frame.height / 2;
  for (const x of draft.centers) drawPoint([x, laneY], 'draft');

  const rect = draftCropRect();
  if (rect) {
    const corners = [[rect.x0, rect.y0], [rect.x1, rect.y0], [rect.x1, rect.y1], [rect.x0, rect.y1]];
    const transform = state.project.transforms.find((t) => t.transform_id === state.panel.display_geometry?.reference_to_rectified_transform_id);
    const unchanged = transform && Number($('rotation').value) === Math.atan2(transform.matrix[1][0], transform.matrix[0][0]) * 180 / Math.PI;
    view.addPolygon(corners.map((p) => unchanged ? inversePoint(transform.matrix, p) : unrotate(p, frame)),
      { class: `${state.dirty ? 'draft ' : ''}row-crop`, 'data-row': state.panel.label });
  }
}

function candidateDescription(candidate) {
  const rect = candidate.crop_rect_rectified;
  const capture = state.project.captures.find((c) => c.capture_id === candidate.selected_exposure_id);
  return `縦範囲 ${rect.y0.toFixed(1)}〜${rect.y1.toFixed(1)} px / ${capture?.exposure_s ?? '?'} 秒`
    + ` / スコア ${candidate.score.toFixed(3)}`;
}

function renderBandRowCandidates() {
  const host = $('band-row-candidates');
  host.replaceChildren();
  for (const candidate of state.region.band_row_candidates || []) {
    const chosen = candidate.candidate_id === state.panel.selected_band_row_candidate_id;
    const row = document.createElement('div');
    row.className = 'band-row-option' + (chosen ? ' chosen' : '');
    row.dataset.candidateId = candidate.candidate_id;
    const description = document.createElement('p');
    description.textContent = `${chosen ? '採用中 / ' : ''}${candidateDescription(candidate)}`;
    const button = document.createElement('button');
    button.textContent = 'この段にする';
    button.disabled = chosen;
    button.onclick = guarded(async () => {
      if (state.dirty) throw Error('候補を切り替える前に編集を保存してください');
      await mutate('select_band_row', { panel_id: state.panel.panel_id, candidate_id: candidate.candidate_id });
      selectPanel(state.panel.panel_id);
      await refreshIssues();
      showMessage('採用する段を切り替えました');
    });
    row.append(description, button);
    host.append(row);
  }
  $('band-row-section').hidden = !host.children.length;
}

function drawBandRowCandidates(frame) {
  const rect = draftCropRect();
  if (!rect) return;
  for (const candidate of state.region.band_row_candidates || []) {
    const chosen = candidate.candidate_id === state.panel.selected_band_row_candidate_id;
    const crop = chosen ? rect : { ...rect, y0: candidate.crop_rect_rectified.y0,
      y1: candidate.crop_rect_rectified.y1 };
    const points = rectCorners(crop).map((p) => unrotate(p, frame));
    view.addPolygon(points, { class: 'band-row-candidate' + (chosen ? ' chosen' : ''),
      'data-candidate-id': candidate.candidate_id });
    const label = svgNode('text', { x: points[0][0], y: points[0][1] - 8, class: 'candidate-label' });
    label.textContent = `${chosen ? '採用中 / ' : ''}スコア ${candidate.score.toFixed(3)}`;
    view.overlays.append(label);
  }
}

function drawMarkerOverlays() {
  const draft = state.annotation_draft;
  drawPath(draft.markerRoi, 'draft');
  for (const point of draft.markerPoints) drawPoint(point, 'draft');
}

async function renderExposures() {
  const { panel, region, project } = state;
  $('exposures').replaceChildren();
  $('preview').removeAttribute('src');
  const set = imageSet(panel.image_set_id || region.image_set_id);
  if (!set || !panel.display_geometry) return;

  const referenceFrameId = panel.display_geometry.reference_frame_id;
  for (const captureId of set.capture_ids) {
    const capture = project.captures.find((c) => c.capture_id === captureId);
    const frame = captureFrame(captureId);
    const aligned = frame.frame_id === referenceFrameId || project.transforms.some((t) =>
      t.kind === 'exposure_to_reference' && t.from_frame_id === frame.frame_id
      && t.to_frame_id === referenceFrameId);

    const row = document.createElement('div');
    const label = document.createElement('span');
    label.textContent = `${capture.exposure_s} 秒 ${aligned ? '' : '位置合わせ未確認'}`;
    row.append(label);

    if (aligned) {
      const image = document.createElement('img');
      image.src = `/api/image/panel/${panel.panel_id}/${captureId}.png?r=${project.project_revision}`;
      row.append(image);
      if (captureId === panel.selected_exposure_id) $('preview').src = image.src;
    }

    const adopt = document.createElement('button');
    adopt.textContent = panel.selected_exposure_id === captureId ? '採用中' : '採用';
    adopt.onclick = guarded(async () => {
      await mutate('set_selected_exposure', { panel_id: panel.panel_id, capture_id: captureId });
      selectPanel(panel.panel_id);
    });
    row.append(adopt);
    $('exposures').append(row);
  }
}

async function refreshIssues() {
  const validation = await request('/api/validate');
  const blockers = validation.output_readiness.blocking_items.map((blocker) => {
    const panel = state.project.panels.find((p) => p.panel_id === blocker.panel_id);
    const named = panel && { ...panel, user_label: regionOf(panel).user_label };
    return WbLabels.describeBlocker(blocker, named);
  });
  // unresolved_items are plain sentences already.
  $('issues').textContent = [...validation.unresolved_items, ...blockers].join(' / ');
}

// ---- editor clicks ----

function annotationDraft(frameId, purpose, geometry) {
  const lane = $('annotation-lane').value;
  return {
    frame_id: frameId,
    panel_id: state.panel.panel_id,
    lane_id: lane ? Number(lane) : null,
    purpose,
    geometry,
    created_by: 'human',
    band_status: $('band-status').value,
    review_status: 'draft',
  };
}

function addLaneCenter(point, frame) {
  const angle = rotationRad();
  const x = frame.width / 2 + Math.cos(angle) * (point[0] - frame.width / 2) - Math.sin(angle) * (point[1] - frame.height / 2);
  const laneInput = $('lane-number');
  state.annotation_draft.known[Number(laneInput.value)] = x;
  laneInput.value = Number(laneInput.value) + 1;
}

// Crop edges alternate: the first click sets the top, the next the bottom.
function addCropEdge(point, frame) {
  const angle = rotationRad();
  const y = frame.height / 2 + Math.sin(angle) * (point[0] - frame.width / 2) + Math.cos(angle) * (point[1] - frame.height / 2);
  if ($('crop-top').value === '' || $('crop-bottom').value !== '') {
    $('crop-top').value = y;
    $('crop-bottom').value = '';
  } else {
    $('crop-bottom').value = y;
  }
}

function onEditorClick(event) {
  if (state.proposal) return;
  const frame = frameById(currentFrameId());
  if (!frame) return;
  const point = view.clientToFrame(event.clientX, event.clientY);
  if (!view.frameContains(point)) return;

  const draft = state.annotation_draft;
  switch ($('tool').value) {
    case 'lanes':
      addLaneCenter(point, frame);
      break;
    case 'crop':
      addCropEdge(point, frame);
      break;
    case 'band':
      draft.annotations.push(annotationDraft(frame.frame_id, 'band', { type: 'point', coordinates: [point] }));
      break;
    case 'marker':
      draft[$('landmark-side').value === 'marker' ? 'markerPoints' : 'referencePoints'].push(point);
      break;
    default:
      draft.points.push(point);
  }
  markDirty();
}

function finishPolygon() {
  const draft = state.annotation_draft;
  if (draft.points.length < 3) {
    showMessage('ポリゴンには3点以上必要です');
    return;
  }
  const tool = $('tool').value;
  if (tool === 'region') {
    draft.polygon = draft.points;
  } else if (tool === 'marker_roi') {
    draft.markerRoi = draft.points;
  } else if (tool === 'measurement_roi' || tool === 'background_roi') {
    draft.annotations.push(annotationDraft($('reference').value, tool, { type: 'polygon', coordinates: draft.points }));
  }
  draft.points = [];
  markDirty();
}

// ---- saving ----

function regionEdit() {
  const draft = state.annotation_draft;
  return {
    image_set_id: $('series').value || null,
    reference_frame_id: $('reference').value || null,
    polygon: draft.polygon.length ? draft.polygon : null,
    lane_count: numberOrNull('lane-count'),
    assignment_status: $('assignment').value,
  };
}

// Null until lanes, both crop edges and the output width are all set.
function displayGeometryEdit() {
  const { centers } = state.annotation_draft;
  const cropEdgesSet = $('crop-top').value !== '' && $('crop-bottom').value !== '';
  if (centers.length === 1 && cropEdgesSet && $('width').value && !$('crop-left').value) {
    throw Error('切出し幅の計算にはレーン中心が2つ以上必要です');
  }
  const rect = draftCropRect();
  if (!rect || !$('width').value) return null;
  const frame = frameById($('reference').value);
  return {
    reference_frame_id: frame.frame_id,
    rotation_deg: Number($('rotation').value),
    rotation_center: state.project.transforms.find((t) =>
      t.transform_id === state.panel.display_geometry?.reference_to_rectified_transform_id)?.evidence?.center
      || [frame.width / 2, frame.height / 2],
    crop_rect: rect,
    target_width_px: Number($('width').value),
    lane_layout: { lane_count: numberOrNull('lane-count'), centers_x: state.annotation_draft.centers,
      status: numberOrNull('lane-count') ? 'confirmed' : 'proposed' },
  };
}

// Moving the reference frame would orphan coordinates saved against the old one.
function assertReferenceFrameCanChange() {
  const { region, panel, project } = state;
  const frameId = $('reference').value;
  const hasCoordinates = region.geometry || panel.display_geometry
    || project.annotations.some((a) => a.panel_id === panel.panel_id);
  if (region.reference_frame_id && region.reference_frame_id !== frameId && hasCoordinates) {
    throw Error('基準画像の変更には既存座標の移行が必要です');
  }
}

async function savePanelEdit() {
  if (state.annotation_draft.points.length) throw Error('作成中のポリゴンを閉じてください');
  assertReferenceFrameCanChange();

  const edit = {
    panel_id: state.panel.panel_id,
    region: regionEdit(),
    annotations: state.annotation_draft.annotations,
  };
  const geometry = displayGeometryEdit();
  if (geometry) edit.display_geometry = geometry;

  await mutate('save_panel_edit', edit);
  selectPanel(state.panel.panel_id);
  await refreshIssues();
  showMessage('保存しました');
}

// ---- other actions ----

async function fitLanes() {
  const result = await request('/api/fit_lanes', { lane_count: Number($('lane-count').value), known: state.annotation_draft.known });
  state.annotation_draft.centers = result.centers;
  $('residuals').textContent = '残差: ' + JSON.stringify(result.residuals);
  markDirty();
}

async function fitMarker() {
  const draft = state.annotation_draft;
  state.markerFit = await request('/api/fit_marker', { marker_points: draft.markerPoints, reference_points: draft.referencePoints });
  $('marker-result').textContent = '残差: ' + JSON.stringify(state.markerFit.residuals);
}

function confirmMarkerIdentity() {
  if (!$('marker-evidence').value.trim()) {
    showMessage('根拠を入力してください');
    return;
  }
  state.markerFit = { matrix: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] };
  $('marker-result').textContent = '同一位置を確認';
}

async function saveMarkerRegistration() {
  if (!state.markerFit) throw Error('対応点の計算または同一位置の確認が必要です');
  await mutate('add_marker_registration', {
    membrane_region_id: state.region.region_id,
    marker_frame_id: $('marker-frame').value,
    matrix: state.markerFit.matrix,
    marker_roi_polygon: state.annotation_draft.markerRoi,
    status: 'confirmed',
    evidence: $('marker-evidence').value,
  });
  selectPanel(state.panel.panel_id);
}

async function confirmSavedAnnotations() {
  const ids = state.project.annotations
    .filter((a) => a.panel_id === state.panel.panel_id && a.review_status === 'draft')
    .map((a) => a.annotation_id);
  await mutate('confirm_annotations', { annotation_ids: ids });
  selectPanel(state.panel.panel_id);
  showMessage('保存済み注釈を確認しました');
}

async function confirmAlignment() {
  await mutate('confirm_identity_alignment', {
    image_set_id: $('series').value,
    reference_frame_id: $('reference').value,
    evidence: $('alignment-evidence').value,
  });
  showMessage('位置合わせを記録しました');
}

async function addRegion() {
  await mutate('add_region', { user_label: $('new-label').value, target_label: 'Actin' });
  selectPanel(state.project.panels.at(-1).panel_id);
}

async function importMetadata() {
  await mutate('import_metadata', { metadata_xlsx: $('metadata').value });
  selectPanel(state.panel.panel_id);
}

async function saveSampleNames() {
  const names = $('samples').value.split('\n').map((line) => line || null);
  await mutate('set_sample_names', { sample_group_id: $('sample-group').value, samples: names });
  showMessage('試料名を保存しました');
}

async function saveControlRelations() {
  const targets = Array.from($('control-for').selectedOptions, (option) => option.value);
  await mutate('set_control_for', { panel_id: state.panel.panel_id, control_for_panel_ids: targets });
  showMessage('対照関係を保存しました');
}

function onSeriesChange() {
  fillReferenceOptions($('series').value, '');
  markDirty();
}

function svgNode(tag, attributes) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function rectCorners(r) {
  return [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];
}

function inversePoint(m, [x, y]) {
  const det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
  const dx = x - m[0][2], dy = y - m[1][2];
  return [(m[1][1] * dx - m[0][1] * dy) / det, (-m[1][0] * dx + m[0][0] * dy) / det];
}

function proposalReason(reason) {
  if (reason?.startsWith('longest exposure with no red')) return 'この範囲に赤い表示なし';
  if (reason?.startsWith('all registered exposures have red')) return '赤い表示が最も少ない露光';
  if (reason?.startsWith('No exposure registered within')) return 'カラーメトリック写真と位置が合わないため除外';
  if (reason?.startsWith('No colorimetric photo')) return 'カラーメトリック写真がないため除外';
  return null;
}

function groupedExclusions(items) {
  const groups = [];
  for (const item of items) {
    const reason = proposalReason(item.reason);
    if (!reason) continue;
    const match = /^(.*?)(\d+)$/.exec(item.image_set_id || '');
    const last = groups.at(-1);
    if (match && last && last.prefix === match[1] && Number(match[2]) === last.end + 1 && last.reason === reason) {
      last.end = Number(match[2]); last.last = item.image_set_id;
    } else {
      groups.push({ first: item.image_set_id, last: item.image_set_id, prefix: match?.[1],
        end: match ? Number(match[2]) : null, reason });
    }
  }
  return groups.map((g) => `${g.first}${g.first === g.last ? '' : '〜' + g.last}: ${g.reason}`);
}
window.wbAnnotate.proposalReason = proposalReason;
window.wbAnnotate.groupedExclusions = groupedExclusions;

function drawProposal() {
  const group = state.proposal.groups[Number($('proposal-group').value) || 0];
  if (!group) { view.clearFrame(); return; }
  const marker = $('proposal-frame').value === 'marker' && !!group.marker_frame_id;
  const frame = frameById(marker ? group.marker_frame_id : group.reference_frame_id);
  const transform = group.transforms.find((t) => t.kind === 'marker_to_reference' && t.from_frame_id === group.marker_frame_id);
  if (!frame || (marker && !transform)) { view.clearFrame(); return; }
  view.setFrame({ frameId: frame.frame_id, width: frame.width, height: frame.height,
    href: `/api/image/frame/${frame.frame_id}.png` });
  view.applyViewport(state.viewport);
  view.clearOverlays();
  const toFrame = (p) => marker ? inversePoint(transform.matrix, p) : p;
  for (const membrane of group.membranes) {
    view.addPolygon(membrane.polygon_reference_frame.map(toFrame), { class: 'proposal-membrane' });
    const angle = -membrane.rotation.degrees * Math.PI / 180;
    const [cx, cy] = membrane.rotation.center;
    const unrect = ([x, y]) => [cx + Math.cos(angle) * (x - cx) - Math.sin(angle) * (y - cy),
      cy + Math.sin(angle) * (x - cx) + Math.cos(angle) * (y - cy)];
    const drawRect = (r) => rectCorners(r).map(unrect).map(toFrame);
    for (const row of membrane.row_candidates) {
      const points = drawRect(row.crop_rect_rectified);
      const chosen = row.candidate_id === membrane.row.candidate_id;
      view.addPolygon(points, { class: 'proposal-row band-row-candidate' + (chosen ? ' chosen' : ''),
        'data-candidate-id': row.candidate_id });
      const text = svgNode('text', { x: points[0][0], y: points[0][1], class: 'proposal-label' });
      text.textContent = `${chosen ? membrane.panel_label + ' / ' : ''}${row.score.toFixed(3)}`;
      view.overlays.append(text);
    }
    if (membrane.marker_lanes) {
      const ys = [membrane.row.crop_rect_rectified.y0, membrane.row.crop_rect_rectified.y1];
      view.addPolygon(drawRect({ x0: membrane.marker_lanes.x0_rectified, x1: membrane.marker_lanes.x1_rectified,
        y0: Math.min(...ys), y1: Math.max(...ys) }), { class: 'proposal-marker' });
    }
  }
}

async function showProposal() {
  state.proposal = await request('/api/propose', {});
  const host = $('proposal-summary'); host.replaceChildren();
  const details = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = '詳細'; details.append(summary);
  for (const group of state.proposal.groups) {
    for (const membrane of group.membranes) {
      const title = document.createElement('strong');
      title.textContent = `${membrane.panel_label}（${membrane.label}）`; host.append(title);
      const row = membrane.row;
      const capture = state.project.captures.find((c) => c.capture_id === row.selected_exposure_id);
      const line = document.createElement('p');
      const reason = proposalReason(row.exposure_reason);
      const rect = row.crop_rect_rectified;
      line.textContent = `採用する段: 縦範囲 ${rect.y0.toFixed(1)}〜${rect.y1.toFixed(1)} px、`
        + `${capture?.exposure_s ?? '?'}秒${reason ? '（' + reason + '）' : ''}`
        + `。ほかの候補 ${membrane.row_candidates.length - 1}段`;
      host.append(line);
      const raw = document.createElement('p');
      raw.textContent = `${membrane.panel_label}: ${row.exposure_reason || ''}`; details.append(raw);
    }
    for (const warning of group.warnings || []) {
      const line = document.createElement('p'); line.textContent = String(warning); details.append(line);
    }
  }
  for (const text of groupedExclusions(state.proposal.excluded_image_sets || [])) {
    const line = document.createElement('p'); line.textContent = text; host.append(line);
  }
  for (const key of ['excluded_image_sets', 'unmatched_marker_frames', 'warnings']) {
    for (const item of state.proposal[key] || []) {
      const line = document.createElement('p');
      line.textContent = typeof item === 'string' ? item : `${item.image_set_id || item.frame_id}: ${item.reason}`;
      details.append(line);
    }
  }
  host.append(details); $('apply-proposal').hidden = false;
  $('proposal-group').replaceChildren(...state.proposal.groups.map((g, i) =>
    new Option(g.image_set_ids.join(' / '), String(i))));
  $('proposal-group-control').hidden = state.proposal.groups.length < 2;
  $('proposal-frame-control').hidden = false; $('proposal-frame').value = 'marker'; drawEditor();
}

async function applyProposal() {
  await mutate('apply_proposal', { proposal: state.proposal });
  $('apply-proposal').hidden = true;
  $('proposal-summary').replaceChildren();
  state.proposal = null; $('proposal-frame-control').hidden = true; $('proposal-group-control').hidden = true;
  if (state.project.panels.length) selectPanel(state.project.panels[0].panel_id);
  await refreshIssues();
}

function renderAlignmentCheck() {
  const host = $('alignment-check');
  host.replaceChildren();
  const panel = state.panel;
  const dg = panel.display_geometry;
  if (!dg) return;
  const registration = state.project.marker_registrations.find((r) => r.marker_registration_id === panel.marker_registration_id);
  const frameIds = new Set((imageSet(panel.image_set_id)?.capture_ids || []).map((id) => captureFrame(id)?.frame_id));
  const transforms = state.project.transforms.filter((t) => t.status === 'candidate' &&
    (t.transform_id === dg.reference_to_rectified_transform_id || t.transform_id === registration?.transform_id ||
      (t.kind === 'exposure_to_reference' && frameIds.has(t.from_frame_id) && t.to_frame_id === dg.reference_frame_id)));
  const seriesTransforms = transforms.filter((t) => t.kind === 'exposure_to_reference');
  if (seriesTransforms.length) {
    const evidence = document.createElement('input'); evidence.placeholder = '系列の確認根拠';
    const button = document.createElement('button'); button.textContent = 'この系列の候補をまとめて確定';
    button.onclick = guarded(async () => {
      if (!evidence.value.trim()) throw Error('確認の根拠を入力してください');
      for (const t of seriesTransforms) await mutate('confirm_transform', { transform_id: t.transform_id, evidence: evidence.value });
      selectPanel(panel.panel_id); await refreshIssues();
    });
    host.append(evidence, button);
  }
  const ref = frameById(dg.reference_frame_id);
  for (const t of transforms) {
    const box = document.createElement('div');
    const names = { marker_to_reference: 'マーカー', exposure_to_reference: '露光', reference_to_rectified: '回転' };
    const title = document.createElement('p');
    title.textContent = `位置合わせ候補: ${names[t.kind]} ${t.kind === 'exposure_to_reference' ? t.from_frame_id : ''}`;
    box.append(title);
    const svg = svgNode('svg', { viewBox: `0 0 ${ref.width} ${ref.height}`, class: 'alignment-overlay' });
    svg.append(svgNode('image', { href: `/api/image/overview/${ref.frame_id}.png`, width: ref.width, height: ref.height }));
    if (t.kind !== 'reference_to_rectified') {
      const source = frameById(t.from_frame_id);
      const m = t.matrix;
      svg.append(svgNode('image', { href: `/api/image/overview/${source.frame_id}.png`, width: source.width,
        height: source.height, opacity: '0.5', transform: `matrix(${m[0][0]} ${m[1][0]} ${m[0][1]} ${m[1][1]} ${m[0][2]} ${m[1][2]})` }));
    }
    if (state.region.geometry) svg.append(svgNode('polygon', {
      points: state.region.geometry.polygon.map((p) => p.join(',')).join(' '),
      fill: 'none', stroke: '#ffdb00', 'stroke-width': 5 }));
    box.append(svg);
    if (t.kind !== 'reference_to_rectified') {
      const toggle = document.createElement('button'); toggle.textContent = '重ね合わせ / 基準画像';
      toggle.onclick = () => { const image = svg.querySelectorAll('image')[1]; image.style.display = image.style.display ? '' : 'none'; };
      box.append(toggle);
    }
    const evidence = document.createElement('input'); evidence.placeholder = '確認の根拠'; box.append(evidence);
    const button = document.createElement('button'); button.textContent = '位置合わせを確定';
    button.onclick = guarded(async () => {
      await mutate('confirm_transform', { transform_id: t.transform_id, evidence: evidence.value });
      selectPanel(panel.panel_id); await refreshIssues();
    });
    box.append(button); host.append(box);
  }
}

// Zoom, pan and tool choices only change the view; the rest edit the draft.
const VIEW_ONLY_INPUTS = ['tool', 'landmark-side', 'zoom', 'pan-x', 'pan-y'];
const DRAFT_INPUTS = ['reference', 'marker-frame', 'rotation', 'crop-left', 'crop-right', 'crop-top', 'crop-bottom'];

function bindEvents() {
  view.svg.addEventListener('click', onEditorClick);
  $('finish-polygon').onclick = finishPolygon;
  $('undo').onclick = () => {
    state.annotation_draft.points.pop();
    drawEditor();
  };
  $('series').onchange = onSeriesChange;
  $('sample-group').onchange = () => { $('samples').value = sampleNames($('sample-group').value); };
  $('marker-identity').onclick = confirmMarkerIdentity;

  $('proposal-frame').onchange = drawEditor;
  $('proposal-group').onchange = drawEditor;
  $('propose').onclick = guarded(showProposal);
  $('apply-proposal').onclick = guarded(applyProposal);
  $('confirm-panel').onclick = guarded(async () => {
    await mutate('confirm_panel', { panel_id: state.panel.panel_id, evidence: '利用者が段の確定ボタンを押して確認' });
    selectPanel(state.panel.panel_id);
    await refreshIssues();
  });
  $('save').onclick = guarded(savePanelEdit);
  $('confirm').onclick = guarded(confirmSavedAnnotations);
  $('align').onclick = guarded(confirmAlignment);
  $('add-region').onclick = guarded(addRegion);
  $('import-metadata').onclick = guarded(importMetadata);
  $('fit-lanes').onclick = guarded(fitLanes);
  $('fit-marker').onclick = guarded(fitMarker);
  $('save-marker').onclick = guarded(saveMarkerRegistration);
  $('save-samples').onclick = guarded(saveSampleNames);
  $('save-control').onclick = guarded(saveControlRelations);

  for (const id of VIEW_ONLY_INPUTS) $(id).addEventListener('change', drawEditor);
  for (const id of DRAFT_INPUTS) $(id).addEventListener('change', markDirty);
}

bindEvents();
guarded(async () => {
  state.project = await request('/api/project');
  if (state.project.panels.length) selectPanel(state.project.panels[0].panel_id);
  else renderRegionList();
  await refreshIssues();
})();
