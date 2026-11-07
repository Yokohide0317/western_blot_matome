<script>
  import { frameUrl, isIncluded } from '../lib/api.js';
  import { apply, inverse, identity, rotation, cropPolygon, rotationSettings, svgMatrix, pointsAttribute } from '../lib/coordinates.js';
  import { dragTop, dragBottom, dragLeft, dragRight, dragAngle, draftBox, turnBetween, zoomedView, minHeight, minWidth } from '../lib/editing.js';
  import { regionDisplayPoints } from '../lib/regionMapping.js';
  import { effectiveMarkerRect, dragMarker } from '../lib/markers.js';
  import Icon from './Icon.svelte';
  export let project;
  export let panel = null;
  export let preview = null;
  export let mode = 'lum';
  export let busy = false;
  export let stage = 'band';
  export let initialDraw = null;
  export let drawRequest = null;
  export let onselect;
  export let onselectregion;
  export let onsave;
  export let onaddmembrane;
  export let onaddselection;
  export let ondrag;
  export let onmarker = null;

  let svg, fit = stage === 'membrane' || initialDraw === 'membrane' ? 'frame' : 'membrane', vb = null, opacity = 0.5, flicker = false;
  let viewerWidth = 1, viewerHeight = 1, drag = null, boxDrag = null, pan = null, suppressClick = false;
  let drawMode = initialDraw, angleOpen = false;
  let live = {angle: 0, left: 0, right: 0, top: 0, bottom: 0}, liveKey = '';

  const find = (list, key, id) => list.find(item => item[key] === id);
  $: dg = panel?.display_geometry;
  $: region = find(project.membrane_regions, 'region_id', panel?.region_id);
  $: fallbackCapture = project.captures.find(c => c.modality === 'colorimetric') || project.captures[project.captures.length - 1];
  $: fallbackFrame = find(project.frames, 'source_asset_id', fallbackCapture?.presentation_source_asset_id) || project.frames[0] || null;
  $: refFrame = find(project.frames, 'frame_id', dg?.reference_frame_id || region?.geometry?.frame_id || region?.reference_frame_id) || fallbackFrame;
  $: rotationTransform = find(project.transforms, 'transform_id', dg?.reference_to_rectified_transform_id);
  $: settings = refFrame && dg?.crop_rect ? rotationSettings(rotationTransform?.matrix || identity(), rotationTransform?.evidence, refFrame) : {degrees: 0, center: [0, 0]};
  $: registration = find(project.marker_registrations, 'marker_registration_id', panel?.marker_registration_id)
    || find(project.marker_registrations, 'membrane_region_id', panel?.region_id);
  $: markerTransform = (registration?.transform_id ? find(project.transforms, 'transform_id', registration.transform_id) : null)
    || project.transforms.find(t => t.kind === 'marker_to_reference' && t.to_frame_id === refFrame?.frame_id)
    || null;
  $: colorCapture = project.captures.find(c => c.modality === 'colorimetric');
  $: colorFrame = find(project.frames, 'source_asset_id', colorCapture?.presentation_source_asset_id);
  $: registeredMarkerFrame = markerTransform?.kind === 'marker_to_reference' && markerTransform.to_frame_id === refFrame?.frame_id ? find(project.frames, 'frame_id', markerTransform.from_frame_id) : null;
  $: markerFrame = registeredMarkerFrame || colorFrame;
  let colorStageKey = '';
  $: if (`${stage}:${markerFrame?.frame_id || ''}` !== colorStageKey) {
    colorStageKey = `${stage}:${markerFrame?.frame_id || ''}`;
    mode = stage === 'membrane' && markerFrame ? 'marker' : 'lum';
  }
  $: if (mode !== 'lum' && !markerFrame) mode = 'lum';
  $: exposure = exposureView(project, preview ?? panel?.selected_exposure_id, refFrame);
  $: display = displayFor(mode, refFrame, exposure, markerFrame, markerTransform);
  $: canMap = display.canMap !== false;
  // Handles stay on screen while a save is running (so keyboard focus survives); they only accept input when not busy.
  $: showHandles = stage === 'band' && !!dg?.crop_rect && mode === 'lum' && !drawMode;
  $: editable = showHandles && !busy;
  $: fromDisplay = inverse(display.toDisplay);
  function exposureView(project, captureId, ref) {
    const capture = find(project.captures, 'capture_id', captureId);
    const frame = find(project.frames, 'source_asset_id', capture?.presentation_source_asset_id);
    if (!frame || !ref) return null;
    if (frame.frame_id === ref.frame_id) return {frame, matrix: identity(), transform: null};
    const transform = project.transforms.find(t => t.kind === 'exposure_to_reference' && t.from_frame_id === frame.frame_id && t.to_frame_id === ref.frame_id);
    return transform ? {frame, matrix: transform.matrix, transform} : null;
  }
  function displayFor(mode, ref, exposure, markerFrame, markerTransform) {
    if (mode === 'marker' && markerFrame) return {frame: markerFrame,
      toDisplay: markerTransform ? inverse(markerTransform.matrix) : identity(),
      canMap: !!markerTransform || markerFrame.frame_id === ref?.frame_id, overlay: null};
    if (mode === 'overlay' && registeredMarkerFrame) return {frame: ref, toDisplay: identity(), overlay: {frame: registeredMarkerFrame, matrix: markerTransform.matrix}};
    if (mode === 'lum' && exposure) return {frame: exposure.frame, toDisplay: inverse(exposure.matrix), overlay: null};
    return {frame: ref, toDisplay: identity(), overlay: null};
  }
  const toDisplay = points => points.map(point => apply(display.toDisplay, point));

  // Live values follow the saved geometry except while a handle or angle slider is being dragged.
  $: if (!drag) syncLive(`${panel?.panel_id}:${dg?.geometry_revision}:${project.project_revision}`);
  function syncLive(key, force = false) {
    if (key === liveKey && !force) return;
    liveKey = key;
    live = {
      angle: settings.degrees,
      left: dg?.crop_rect?.x0 ?? 0,
      right: dg?.crop_rect?.x1 ?? 0,
      top: dg?.crop_rect?.y0 ?? 0,
      bottom: dg?.crop_rect?.y1 ?? 0,
    };
  }
  $: liveRect = dg?.crop_rect ? {x0: live.left, x1: live.right, y0: live.top, y1: live.bottom} : null;
  $: chosen = canMap && liveRect ? toDisplay(cropPolygon(liveRect, rotation(live.angle, settings.center))) : null;
  // The marker is edited on the Color/overlay view, in the Selection's rectified frame like the crop.
  // Only x is its own; its top and bottom are the band crop's, so those handles move the crop.
  $: savedMarker = effectiveMarkerRect(project, panel);
  let markerLive = null, markerDrag = null, markerKey = '';
  $: if (!markerDrag) syncMarker(`${panel?.panel_id}:${project.project_revision}`, savedMarker);
  function syncMarker(key, rect) {
    if (key === markerKey && markerLive) return;
    markerKey = key;
    markerLive = rect;
  }
  $: markerCrop = canMap && mode !== 'lum' && stage === 'band' && markerLive && liveRect
    ? toDisplay(cropPolygon({x0: markerLive.x0, x1: markerLive.x1, y0: live.top, y1: live.bottom}, rotation(live.angle, settings.center)))
    : null;
  $: markerShown = !!markerCrop && !!onmarker && !drawMode && !!dg?.crop_rect;
  $: markerEditable = markerShown && !busy;
  $: markerHandles = markerShown ? handlePoints(markerCrop, scale) : null;
  $: draft = boxDrag ? draftBox(boxDrag.start, boxDrag.current, refFrame, boxDrag.angle, boxDrag.center) : null;
  $: draftPoints = draft ? toDisplay(cropPolygon(draft.rect, rotation(boxDrag.angle, boxDrag.center))) : null;

  $: others = panelsOfRegion(project, panel);
  function panelsOfRegion(project, panel) {
    return project.panels.filter(p => panel && p.region_id === panel.region_id && p.panel_id !== panel.panel_id && isIncluded(p));
  }
  $: otherCrops = (canMap ? others : []).filter(p => p.display_geometry?.crop_rect).map(p => {
    const transform = find(project.transforms, 'transform_id', p.display_geometry.reference_to_rectified_transform_id);
    return transform ? {id: p.panel_id, points: toDisplay(cropPolygon(p.display_geometry.crop_rect, transform.matrix))} : null;
  }).filter(Boolean);
  $: membranes = project.membrane_regions.map(r => ({
    id: r.region_id, label: r.user_label, selected: r.region_id === panel?.region_id,
    included: r.included,
    points: regionDisplayPoints(project, r, {mode, canMap, referenceFrameId: refFrame?.frame_id,
      displayFrameId: display.frame?.frame_id, toDisplay: display.toDisplay, markerTransform}),
  })).filter(membrane => membrane.points);

  $: handles = showHandles && chosen ? handlePoints(chosen, scale) : null;
  function handlePoints(poly, scale) {
    const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const center = mid(poly[0], poly[2]), right = mid(poly[1], poly[2]), left = mid(poly[0], poly[3]);
    const dx = right[0] - center[0], dy = right[1] - center[1], length = Math.hypot(dx, dy) || 1;
    const reach = 26 / scale;
    return {top: mid(poly[0], poly[1]), bottom: mid(poly[3], poly[2]), left, right, edge: right,
      rotate: [right[0] + dx / length * reach, right[1] + dy / length * reach]};
  }

  function focusBounds(points) {
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const margin = Math.max(x1 - x0, y1 - y0) * 0.12;
    return {x: x0 - margin, y: y0 - margin, width: x1 - x0 + margin * 2, height: y1 - y0 + margin * 2};
  }
  $: focus = membranes.find(m => m.selected)?.points;
  $: whole = {x: 0, y: 0, width: display.frame?.width || 1, height: display.frame?.height || 1};
  $: base = fit === 'membrane' && focus ? focusBounds(focus) : whole;
  $: fitMode = fit === 'membrane' && focus ? 'membrane' : 'frame';
  $: view = vb || base;
  $: scale = Math.min(viewerWidth / view.width, viewerHeight / view.height) || 1;
  $: zoom = base.width / view.width;
  // A new selection, view or fit starts from the base rectangle again.
  $: resetKey = `${panel?.panel_id}|${mode}|${fit}|${display.frame?.frame_id}`;
  let lastReset = '';
  $: if (resetKey !== lastReset) { lastReset = resetKey; vb = null; }

  function svgPoint(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const mapped = point.matrixTransform(svg.getScreenCTM().inverse());
    return [mapped.x, mapped.y];
  }
  function wheelZoom(node) {
    const handler = event => {
      event.preventDefault();
      vb = zoomedView(view, base, svgPoint(event), Math.exp(-event.deltaY * 0.0018));
    };
    node.addEventListener('wheel', handler, {passive: false});
    return {destroy: () => node.removeEventListener('wheel', handler)};
  }

  // Do not carry an unfinished crop or angle edit into another membrane.
  let lastContext = `${stage}:${panel?.region_id}:${panel?.panel_id}`;
  $: if (`${stage}:${panel?.region_id}:${panel?.panel_id}` !== lastContext) {
    lastContext = `${stage}:${panel?.region_id}:${panel?.panel_id}`;
    drawMode = null; angleOpen = false; boxDrag = null; drag = null; pan = null; vb = null;
    ondrag(false);
    fit = stage === 'membrane' ? 'frame' : 'membrane';
  }

  function toggleDraw(kind) {
    if (kind === 'membrane' && stage !== 'membrane') return;
    angleOpen = false;
    drawMode = drawMode === kind ? null : kind;
    if (drawMode === 'membrane') fit = 'frame';
    else if (drawMode === 'selection') { mode = 'lum'; fit = 'membrane'; }
  }
  let lastDrawRequest = null;
  $: if (drawRequest && drawRequest !== lastDrawRequest) {
    lastDrawRequest = drawRequest;
    drawMode = drawRequest.kind;
    angleOpen = false;
    if (drawMode === 'selection') { mode = 'lum'; fit = 'membrane'; }
    else { fit = 'frame'; if (!registeredMarkerFrame && markerFrame?.frame_id !== refFrame?.frame_id) mode = 'lum'; }
  }
  function onWindowKey(event) {
    if (event.key === 'Escape' && (drawMode || angleOpen || boxDrag)) {
      drawMode = null; angleOpen = false; boxDrag = null; ondrag(false);
    }
  }

  async function saveCurrentGeometry(angleValue) {
    if (!panel || !dg?.crop_rect) return false;
    const ok = await onsave(panel.panel_id, {
      reference_frame_id: dg.reference_frame_id,
      rotation_deg: angleValue,
      rotation_center: settings.center,
      crop_rect: {x0: live.left, y0: live.top, x1: live.right, y1: live.bottom},
      target_width_px: dg.target_width_px,
      lane_layout: dg.lane_layout,
    });
    if (!ok) syncLive(liveKey, true);
    return ok;
  }
  async function setAngle(target) {
    if (!panel || !dg?.crop_rect || !refFrame || busy) return;
    const state = {rect: dg.crop_rect, left: live.left, right: live.right, top: live.top, bottom: live.bottom, angle: live.angle};
    const clamped = Number(dragAngle(target, state, settings.center, refFrame).toFixed(2));
    live = {...live, angle: clamped};
    if (Math.abs(clamped - settings.degrees) < 1e-4) return;
    await saveCurrentGeometry(clamped);
  }
  const stepAngle = delta => setAngle(live.angle + delta);
  function scrubAngle(event) {
    if (!dg?.crop_rect || !refFrame) return;
    ondrag(true);
    const state = {rect: dg.crop_rect, left: live.left, right: live.right, top: live.top, bottom: live.bottom, angle: live.angle};
    live = {...live, angle: dragAngle(Number(event.currentTarget.value), state, settings.center, refFrame)};
  }
  async function commitScrubAngle() {
    ondrag(false);
    await setAngle(live.angle);
  }

  // Handles live on whichever frame is shown; edits are always expressed in reference coordinates.
  const referencePoint = event => apply(fromDisplay, svgPoint(event));
  const drawPoint = (event, kind) => {
    const ref = referencePoint(event);
    return kind === 'selection' && dg?.crop_rect ? apply(rotation(live.angle, settings.center), ref) : ref;
  };
  function startHandle(kind, event, fromMarker = false) {
    if (!(editable || (fromMarker && markerEditable)) || event.button !== 0) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = referencePoint(event), rectified = apply(rotation(live.angle, settings.center), point);
    const pivot = settings.center;
    const grab = kind === 'top' ? live.top - rectified[1]
      : kind === 'bottom' ? live.bottom - rectified[1]
      : kind === 'left' ? live.left - rectified[0]
      : kind === 'right' ? live.right - rectified[0] : 0;
    drag = {kind, moved: false, min: minHeight(live), minW: minWidth({...live, rect: dg.crop_rect}), start: {...live},
      angle: Math.atan2(point[1] - pivot[1], point[0] - pivot[0]), grab};
    ondrag(true);
  }
  function moveHandle(event) {
    const point = referencePoint(event), center = settings.center;
    const state = {rect: dg.crop_rect, left: live.left, right: live.right, top: live.top, bottom: live.bottom, angle: live.angle, min: drag.min, minW: drag.minW};
    if (drag.kind === 'rotate') {
      const pivot = Math.hypot(point[0] - center[0], point[1] - center[1]) > 1e-6;
      if (!pivot) return;
      const turn = turnBetween(drag.angle, Math.atan2(point[1] - center[1], point[0] - center[0]));
      live = {...live, angle: dragAngle(drag.start.angle - turn, state, center, refFrame)};
    } else if (drag.kind === 'left' || drag.kind === 'right') {
      const x = apply(rotation(live.angle, center), point)[0] + drag.grab;
      live = drag.kind === 'left' ? {...live, left: dragLeft(x, state, center, refFrame)} : {...live, right: dragRight(x, state, center, refFrame)};
    } else {
      const y = apply(rotation(live.angle, center), point)[1] + drag.grab;
      live = drag.kind === 'top' ? {...live, top: dragTop(y, state, center, refFrame)} : {...live, bottom: dragBottom(y, state, center, refFrame)};
    }
    drag.moved = drag.moved || live.angle !== drag.start.angle || live.top !== drag.start.top || live.bottom !== drag.start.bottom
      || live.left !== drag.start.left || live.right !== drag.start.right;
  }
  async function endHandle() {
    const finished = drag;
    if (finished.moved) {
      const ok = await saveCurrentGeometry(live.angle);
      if (!ok) { drag = null; syncLive(liveKey, true); }
    }
    drag = null;
    ondrag(false);
  }

  const rectifiedPoint = event => apply(rotation(live.angle, settings.center), referencePoint(event));
  function startMarker(kind, event) {
    if (!markerEditable || event.button !== 0) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    const [x] = rectifiedPoint(event);
    const edge = {left: markerLive.x0 - x, right: markerLive.x1 - x, move: -x};
    markerDrag = {kind, start: {...markerLive}, grab: edge[kind], moved: false};
    ondrag(true);
  }
  function moveMarker(event) {
    const [x] = rectifiedPoint(event);
    markerLive = dragMarker(markerDrag.kind, markerDrag.start, x + markerDrag.grab);
    markerDrag.moved = true;
  }
  async function endMarker() {
    const finished = markerDrag;
    markerDrag = null;
    ondrag(false);
    if (finished.moved && !(await onmarker(panel.panel_id, {x0: markerLive.x0, x1: markerLive.x1}))) markerLive = finished.start;
  }
  // Arrow keys on a focused handle: 1 px (Shift: 10 px) or 0.1° (Shift: 1°); saved after a short pause or on blur.
  let keyTimer;
  const scheduleKeySave = () => { clearTimeout(keyTimer); keyTimer = setTimeout(keyRelease, 600); };
  const arrows = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
  function keyHandle(kind, event, fromMarker = false) {
    const step = arrows[event.key];
    if (!step || !(editable || (fromMarker && markerEditable))) return;
    const horizontal = kind === 'left' || kind === 'right';
    const delta = kind === 'rotate' ? step[0] || -step[1] : horizontal ? step[0] : step[1];
    if (!delta) return;
    event.preventDefault();
    event.stopPropagation();
    if (!drag) {
      drag = {kind, keyboard: true, moved: false, min: minHeight(live), minW: minWidth({...live, rect: dg.crop_rect}), start: {...live}};
      ondrag(true);
    }
    const center = settings.center, size = event.shiftKey ? 10 : 1;
    const state = {rect: dg.crop_rect, left: live.left, right: live.right, top: live.top, bottom: live.bottom, angle: live.angle, min: drag.min, minW: drag.minW};
    if (kind === 'rotate') live = {...live, angle: dragAngle(live.angle + delta * size / 10, state, center, refFrame)};
    else if (kind === 'left') live = {...live, left: dragLeft(live.left + delta * size, state, center, refFrame)};
    else if (kind === 'right') live = {...live, right: dragRight(live.right + delta * size, state, center, refFrame)};
    else if (kind === 'top') live = {...live, top: dragTop(live.top + delta * size, state, center, refFrame)};
    else live = {...live, bottom: dragBottom(live.bottom + delta * size, state, center, refFrame)};
    drag.moved = true;
    scheduleKeySave();
  }
  function keyMarker(kind, event) {
    const step = arrows[event.key]?.[0];
    if (!step || !markerEditable) return;
    event.preventDefault();
    event.stopPropagation();
    if (!markerDrag) { markerDrag = {kind, keyboard: true, start: {...markerLive}, moved: false}; ondrag(true); }
    const delta = step * (event.shiftKey ? 10 : 1);
    markerLive = dragMarker(kind, markerLive, kind === 'left' ? markerLive.x0 + delta : kind === 'right' ? markerLive.x1 + delta : delta);
    markerDrag.moved = true;
    scheduleKeySave();
  }
  function keyRelease() {
    clearTimeout(keyTimer);
    if (markerDrag?.keyboard) endMarker();
    else if (drag?.keyboard) endHandle();
  }
  async function resetMarker() {
    if (panel && onmarker) await onmarker(panel.panel_id, null);
  }

  function startPan(event) {
    if (event.button !== 0) return;
    if (drawMode && refFrame && !busy) {
      event.stopPropagation();
      const pt = drawPoint(event, drawMode);
      boxDrag = {
        kind: drawMode, start: pt, current: pt,
        angle: drawMode === 'selection' && dg?.crop_rect ? live.angle : 0,
        center: drawMode === 'selection' && dg?.crop_rect ? settings.center : [0, 0],
      };
      svg.setPointerCapture(event.pointerId);
      ondrag(true);
      return;
    }
    pan = {x: event.clientX, y: event.clientY, view: {...view}, moved: false};
  }
  function movePan(event) {
    if (markerDrag) return moveMarker(event);
    if (drag) return moveHandle(event);
    if (boxDrag) {
      boxDrag = {...boxDrag, current: drawPoint(event, boxDrag.kind)};
      return;
    }
    if (!pan) return;
    const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
    if (!pan.moved && Math.hypot(dx, dy) < 4) return;
    if (!pan.moved) { pan.moved = true; svg.setPointerCapture(event.pointerId); }
    if (!vb) return;
    vb = {...pan.view, x: pan.view.x - dx / scale, y: pan.view.y - dy / scale};
  }
  async function endPan() {
    if (markerDrag) return endMarker();
    if (drag) return endHandle();
    if (boxDrag) {
      const finished = boxDrag;
      const result = draftBox(finished.start, finished.current, refFrame, finished.angle, finished.center);
      boxDrag = null;
      ondrag(false);
      suppressClick = true; setTimeout(() => suppressClick = false);
      if (result?.valid) {
        drawMode = null;
        if (finished.kind === 'membrane' && onaddmembrane) await onaddmembrane(result.rect, refFrame?.frame_id);
        else if (finished.kind === 'selection' && panel && onaddselection) await onaddselection(panel.panel_id, result.rect);
      }
      return;
    }
    if (pan?.moved) { suppressClick = true; setTimeout(() => suppressClick = false); }
    pan = null;
  }
  const clicked = action => event => { if (!suppressClick && !drawMode) { event.stopPropagation(); action(); } };
  const keyed = action => event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); action(); } };
  const handleNames = {top: '上端', bottom: '下端', left: '左端', right: '右端', rotate: '回転'};
  const markerHandleNames = {top: '上端（バンドと連動）', bottom: '下端（バンドと連動）', left: 'マーカーの左端', right: 'マーカーの右端'};
  const reset = () => { vb = null; };

  $: angleLabel = `${Number(live.angle.toFixed(1))}°`;
</script>

<svelte:window on:keydown={onWindowKey}/>

<div class="source">
  <div class="bar">
    <div class="angle-wrap">
      <span class="pill" title="膜と傾き">
        {region?.user_label ?? '新規'}{#if stage === 'band' && panel?.panel_id} / Selection {panel.label || panel.panel_id}{/if}
        {#if stage === 'band' && dg?.crop_rect}
          ·
          <button type="button" class="step" aria-label="角度を左へ回す" title="−0.5° (Shift: −0.1°)" disabled={busy}
            on:click={event => stepAngle(event.shiftKey ? -0.1 : -0.5)}>−</button>
          <button type="button" class="angle-btn" aria-label="角度を調整" aria-expanded={angleOpen} disabled={busy}
            on:click={() => angleOpen = !angleOpen}><b data-testid="angle">{angleLabel}</b></button>
          <button type="button" class="step" aria-label="角度を右へ回す" title="+0.5° (Shift: +0.1°)" disabled={busy}
            on:click={event => stepAngle(event.shiftKey ? 0.1 : 0.5)}>+</button>
        {/if}
      </span>
      {#if angleOpen && stage === 'band' && dg?.crop_rect}
        <div class="angle-pop" role="group" aria-label="角度調整">
          <button type="button" class="mini" disabled={busy} on:click={() => stepAngle(-2)}>−2°</button>
          <input class="angle-slider" type="range" min="-25" max="25" step="0.1" aria-label="セレクションの角度"
            value={live.angle} disabled={busy} on:input={scrubAngle} on:change={commitScrubAngle}>
          <button type="button" class="mini" disabled={busy} on:click={() => stepAngle(2)}>+2°</button>
          <button type="button" class="mini" aria-label="角度を0度に戻す" disabled={busy} on:click={() => setAngle(0)}>0°</button>
        </div>
      {/if}
    </div>
    {#if vb}<button class="icon" aria-label="表示をリセット" title="表示をリセット" on:click={reset}>↺</button>{/if}
    <div class="seg" role="group" aria-label="表示">
      <button class="view" aria-label="発光" title="発光" aria-pressed={mode === 'lum'} on:click={() => mode = 'lum'}><span class="dot lum"></span></button>
      <button class="view" aria-label="マーカー" title="マーカー（Colorimetric）" aria-pressed={mode === 'marker'} disabled={!markerFrame} on:click={() => mode = 'marker'}><span class="dot rainbow"></span></button>
      <button class="view" aria-label="Merge" title="Merge（発光とマーカーを重ねる）" aria-pressed={mode === 'overlay'} disabled={!registeredMarkerFrame} on:click={() => mode = 'overlay'}><span class="dot lum"></span><span class="dot rainbow merged"></span></button>
    </div>
    {#if mode === 'overlay'}
      <input class="opacity" type="range" min="0" max="1" step="0.05" aria-label="重ね合わせの濃さ" bind:value={opacity}>
      <button class="icon" aria-pressed={flicker} aria-label="交互表示" title="交互表示" on:click={() => flicker = !flicker}>⇄</button>
    {/if}
    {#if stage === 'band' && region?.included}
      <button type="button" aria-pressed={drawMode === 'selection'} aria-label="セレクションを追加" title="Selectionを追加"
        disabled={busy || !refFrame} on:click={() => toggleDraw('selection')}>＋</button>
    {/if}
    {#if markerEditable}
      {#if dg?.marker_x}<button type="button" class="mini" disabled={busy} on:click={resetMarker}>マーカーを自動に戻す</button>{/if}
    {/if}
    {#if drawMode}
      <span class="draw-hint">{drawMode === 'membrane' ? 'ドラッグで膜を囲む' : 'ドラッグでSelectionを囲む'}</span>
    {/if}
    <span class="grow"></span>
    <div class="seg" role="group" aria-label="範囲">
      <button class="view" aria-label="膜" title="膜に合わせて拡大" aria-pressed={fit === 'membrane'} disabled={!focus} on:click={() => fit = 'membrane'}><Icon name="zoom-membrane"/></button>
      <button class="view" aria-label="全体" title="画像全体を表示" aria-pressed={fit === 'frame'} on:click={() => fit = 'frame'}><Icon name="zoom-whole"/></button>
    </div>
  </div>
  <div class="stage">
    {#if mode === 'marker' && !canMap}
      <span class="draw-hint" role="status">マーカー画像を表示中。位置合わせがないため、膜の範囲は重ねて表示できません。</span>
    {/if}
    {#if display.frame}
      <svg bind:this={svg} class="canvas" viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`} preserveAspectRatio="xMidYMid meet"
        bind:clientWidth={viewerWidth} bind:clientHeight={viewerHeight} data-fit={fitMode} data-zoom={zoom.toFixed(3)} data-mode={mode}
        data-draw={drawMode || ''} class:zoomed={!!vb && !drawMode} class:panning={pan?.moved} class:drawing={!!drawMode}
        aria-label="元画像" role="group" use:wheelZoom
        on:pointerdown={startPan} on:pointermove={movePan} on:pointerup={endPan} on:pointercancel={endPan} on:dblclick={reset}>
        <image preserveAspectRatio="none" href={frameUrl(display.frame.frame_id)} width={display.frame.width} height={display.frame.height}/>
        {#if display.overlay}
          <image preserveAspectRatio="none" class="overlay" class:flicker href={frameUrl(display.overlay.frame.frame_id)} width={display.overlay.frame.width}
            height={display.overlay.frame.height} transform={svgMatrix(display.overlay.matrix)} style={`opacity:${opacity}`} pointer-events="none"/>
        {/if}
        {#each membranes as m (m.id)}
          <polygon class="membrane" class:selected={m.selected} class:excluded={!m.included} class:passive={!!drawMode || stage === 'band'} data-region={m.id} points={pointsAttribute(m.points)} vector-effect="non-scaling-stroke"
            role={stage === 'band' ? 'presentation' : 'button'} tabindex={stage === 'band' ? '-1' : '0'} aria-label={`膜 ${m.label}`} on:click={clicked(() => stage !== 'band' && onselectregion(m.id))}
            on:keydown={keyed(() => stage !== 'band' && onselectregion(m.id))}/>
        {/each}
        {#if stage === 'band'}
          {#each otherCrops as c (c.id)}
            <polygon class="other-crop" class:passive={!!drawMode} data-panel={c.id} points={pointsAttribute(c.points)} vector-effect="non-scaling-stroke"
              role="button" tabindex="0" aria-label="別の段" on:click={clicked(() => onselect(c.id))} on:keydown={keyed(() => onselect(c.id))}/>
          {/each}
          {#if markerCrop}<polygon class="marker-crop" class:editable={markerEditable} data-marker-crop points={pointsAttribute(markerCrop)} vector-effect="non-scaling-stroke"
            role="slider" tabindex={markerShown ? 0 : -1} aria-label="マーカーの位置" aria-orientation="horizontal"
            aria-valuemin={0} aria-valuemax={Math.round(refFrame?.width || 0)} aria-valuenow={Math.round(markerLive.x0)}
            on:pointerdown={event => startMarker('move', event)} on:keydown={event => keyMarker('move', event)} on:blur={keyRelease}/>{/if}
          {#if chosen}<polygon class="chosen" points={pointsAttribute(chosen)} vector-effect="non-scaling-stroke"/>{/if}
        {/if}
        {#if draftPoints}<polygon class="draft-box" class:invalid={!draft?.valid} points={pointsAttribute(draftPoints)} vector-effect="non-scaling-stroke"/>{/if}
        {#each membranes as m (m.id)}
          <text class="tag" x={Math.min(...m.points.map(p => p[0])) + 5 / scale} y={Math.min(...m.points.map(p => p[1])) + 16 / scale} font-size={13 / scale}>{m.label}</text>
        {/each}
        {#if markerHandles}
          {#each [['left', markerHandles.left], ['right', markerHandles.right]] as [kind, point]}
            <rect class="side-handle marker" data-marker-handle={kind} x={point[0] - 4 / scale} y={point[1] - 9 / scale}
              width={8 / scale} height={18 / scale} rx={2.5 / scale} vector-effect="non-scaling-stroke"
              role="slider" tabindex="0" aria-label={markerHandleNames[kind]} aria-orientation="horizontal"
              aria-valuemin={0} aria-valuemax={Math.round(refFrame?.width || 0)} aria-valuenow={Math.round(kind === 'left' ? markerLive.x0 : markerLive.x1)}
              on:pointerdown={event => startMarker(kind, event)} on:keydown={event => keyMarker(kind, event)} on:blur={keyRelease}/>
          {/each}
          {#each [['top', markerHandles.top], ['bottom', markerHandles.bottom]] as [kind, point]}
            <circle class="handle marker" data-marker-handle={kind} cx={point[0]} cy={point[1]} r={7 / scale} vector-effect="non-scaling-stroke"
              role="slider" tabindex="0" aria-label={markerHandleNames[kind]} aria-orientation="vertical"
              aria-valuemin={0} aria-valuemax={Math.round(refFrame?.height || 0)} aria-valuenow={Math.round(live[kind])}
              on:pointerdown={event => startHandle(kind, event, true)} on:keydown={event => keyHandle(kind, event, true)} on:blur={keyRelease}/>
          {/each}
        {/if}
        {#if handles}
          <line class="stem" x1={handles.edge[0]} y1={handles.edge[1]} x2={handles.rotate[0]} y2={handles.rotate[1]} vector-effect="non-scaling-stroke"/>
          {#each [['left', handles.left], ['right', handles.right]] as [kind, point]}
            <rect class={`side-handle ${kind}`} data-side-handle={kind} x={point[0] - 4 / scale} y={point[1] - 9 / scale}
              width={8 / scale} height={18 / scale} rx={2.5 / scale} vector-effect="non-scaling-stroke"
              role="slider" tabindex="0" aria-label={handleNames[kind]} aria-orientation="horizontal"
              aria-valuemin={0} aria-valuemax={Math.round(refFrame?.width || 0)} aria-valuenow={Math.round(live[kind])}
              on:pointerdown={event => startHandle(kind, event)} on:keydown={event => keyHandle(kind, event)} on:blur={keyRelease}/>
          {/each}
          {#each [['top', handles.top], ['bottom', handles.bottom], ['rotate', handles.rotate]] as [kind, point]}
            <circle class={`handle ${kind}`} data-handle={kind} cx={point[0]} cy={point[1]} r={8 / scale} vector-effect="non-scaling-stroke"
              role="slider" tabindex="0" aria-label={handleNames[kind]} aria-orientation={kind === 'rotate' ? 'horizontal' : 'vertical'}
              aria-valuemin={kind === 'rotate' ? -25 : 0} aria-valuemax={kind === 'rotate' ? 25 : Math.round(refFrame?.height || 0)}
              aria-valuenow={kind === 'rotate' ? Number(live.angle.toFixed(1)) : Math.round(live[kind])}
              aria-valuetext={kind === 'rotate' ? angleLabel : undefined}
              on:pointerdown={event => startHandle(kind, event)} on:keydown={event => keyHandle(kind, event)} on:blur={keyRelease}/>
          {/each}
        {/if}
      </svg>
    {:else}<div class="blank">画像未選択</div>{/if}
  </div>
</div>

<style>
  .source { display: flex; flex-direction: column; min-height: 0; height: 100%; }
  .bar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; flex-wrap: wrap; border-bottom: 1px solid var(--border); background: #fff; }
  .grow { flex: 1; }
  .angle-wrap { position: relative; display: inline-flex; align-items: center; }
  .pill { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-meta); background: #eef3f4; border-radius: 99px; padding: 2px 9px; white-space: nowrap; }
  .step, .angle-btn { border: 0; background: transparent; padding: 1px 5px; font-size: var(--text-meta); line-height: 1.2; border-radius: 5px; color: #193238; }
  .step:hover:not(:disabled), .angle-btn:hover:not(:disabled) { background: #dce8ea; }
  .angle-btn[aria-expanded='true'] { background: var(--teal); color: #fff; }
  .angle-btn b { font-weight: 600; }
  .angle-pop { position: absolute; top: calc(100% + 5px); left: 0; z-index: 20; display: flex; align-items: center; gap: 6px; padding: 6px 9px;
    background: #fff; border: 1px solid #c5d2d7; border-radius: 8px; box-shadow: 0 6px 18px rgba(15, 35, 42, 0.14); }
  .angle-slider { width: 132px; padding: 0; }
  .mini { font-size: var(--text-meta); padding: 2px 6px; border-radius: 5px; white-space: nowrap; }
  .draw-hint { font-size: var(--text-meta); color: #0b6b5f; background: #eaf7f3; border-radius: 99px; padding: 2px 9px; white-space: nowrap; }
  .seg { display: inline-flex; border: 1px solid #c8d3d8; border-radius: 7px; overflow: hidden; }
  .seg button { border: 0; border-radius: 0; padding: 3px 11px; font-size: var(--text-meta); background: #fff; }
  .seg button[aria-pressed='true'] { background: var(--teal); color: #fff; }
  .seg button + button { border-left: 1px solid #c8d3d8; }
  .seg .view { display: inline-flex; align-items: center; justify-content: center; min-width: 38px; height: 26px; }
  .seg .view:disabled .dot { opacity: .35; }
  .dot { display: inline-block; width: 13px; height: 13px; border-radius: 50%; box-shadow: 0 0 0 1.5px #fff; }
  .dot.lum { background: #111; }
  .dot.rainbow { background: conic-gradient(#ff4d4d, #ffb84d, #f5e663, #5fd16a, #4db8ff, #7a5cff, #ff4dd2, #ff4d4d); }
  .dot.merged { margin-left: -6px; }
  .opacity { width: 80px; padding: 0; }
  .icon[aria-pressed='true'] { background: #e1f1f1; }
  .confirm { font-size: var(--text-meta); padding: 3px 11px; border-radius: 99px; color: #0b6b5f; border-color: #8fc5bb; background: #eaf7f3; }
  .stage { flex: 1; min-height: 340px; background: #1c2428; position: relative; }
  .canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; cursor: crosshair; user-select: none; }
  .canvas.zoomed { cursor: grab; }
  .canvas.panning { cursor: grabbing; }
  .canvas.drawing { cursor: crosshair; }
  .blank { color: #6d7f87; display: grid; place-items: center; height: 100%; font-size: var(--text-label); }
  polygon { fill: transparent; stroke-width: 2; }
  polygon.passive { pointer-events: none; }
  polygon.membrane { stroke: #62c1b6; fill: #62c1b608; cursor: pointer; }
  polygon.membrane.selected { stroke: #7fe0d4; stroke-width: 2.5; }
  polygon.membrane.excluded { stroke: #888; stroke-dasharray: 7 5; fill: transparent; opacity: .65; }
  polygon.other-crop { stroke: #8fd0c8; stroke-width: 1.5; cursor: pointer; }
  polygon.marker-crop { stroke: #ff9a5a; stroke-width: 2; stroke-dasharray: 5 4; fill: #ff9a5a18; pointer-events: none; }
  polygon.marker-crop.editable { pointer-events: auto; cursor: ew-resize; }
  .handle.marker, .side-handle.marker { stroke: #e06a22; }

  polygon.chosen { stroke: #36e0d6; stroke-width: 3; fill: #36e0d618; pointer-events: none; }
  polygon.draft-box { stroke: #4df2c8; stroke-width: 2.5; stroke-dasharray: 6 4; fill: #4df2c822; pointer-events: none; }
  polygon.draft-box.invalid { stroke: #f08a6c; fill: #f08a6c22; }
  .tag { fill: #d9fffa; font-weight: 700; paint-order: stroke; stroke: #0d2a2d; stroke-width: 3px; pointer-events: none; }
  .stem { stroke: #36e0d6; stroke-width: 2; pointer-events: none; }
  .handle { fill: #fff; stroke: #0aa8a0; stroke-width: 2.5; cursor: ns-resize; }
  .handle.rotate { cursor: grab; fill: #36e0d6; }
  .side-handle { fill: #fff; stroke: #0aa8a0; stroke-width: 2; cursor: ew-resize; }
  /* Keyboard focus on the image: a bright ring that differs from the teal crop and orange marker. */
  polygon:focus, .handle:focus, .side-handle:focus { outline: none; }
  .canvas polygon:focus-visible { stroke: #ffd23f; stroke-width: 3.5; }
  .canvas .handle:focus-visible, .canvas .side-handle:focus-visible { stroke: #ffd23f; stroke-width: 4; }
  .flicker { animation: flicker 1.5s steps(1) infinite; }
  @keyframes flicker { 50% { opacity: 0 !important; } }
  @media (prefers-reduced-motion: reduce) { .flicker { animation-duration: 4s; } }
</style>
