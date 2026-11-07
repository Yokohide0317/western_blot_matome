<script>
  import { panelUrl, markerUrl } from '../lib/api.js';
  import Icon from './Icon.svelte';
  export let project;
  export let panel;
  export let selected = false;
  export let readonly = false;
  export let preview = null;
  export let canUp = true;
  export let canDown = true;
  export let busy = false;
  export let onselect;
  export let onrename;
  export let onadd;
  export let ondelete;
  export let onsetincluded;
  export let onmove;
  export let onlaneedges;
  export let onediting;
  export let widthScale = 1;

  $: dg = panel.display_geometry;
  $: membraneLabel = project.membrane_regions.find(r => r.region_id === panel.region_id)?.user_label || panel.region_id;
  $: registration = project.marker_registrations.find(r => r.marker_registration_id === panel.marker_registration_id && r.membrane_region_id === panel.region_id)
    || project.marker_registrations.find(r => r.membrane_region_id === panel.region_id && r.marker_roi);
  $: markerTransform = project.transforms.find(t => t.transform_id === registration?.transform_id);
  $: markerRev = `${project.project_revision}-${dg?.geometry_revision ?? 0}-${registration?.marker_registration_id ?? ''}-${JSON.stringify(dg?.marker_x ?? null)}`;
  let markerFailed = false, lastMarkerRev = '';
  $: if (markerRev !== lastMarkerRev) { lastMarkerRev = markerRev; markerFailed = false; }
  $: hasMarker = !!(dg?.crop_rect && registration?.marker_roi && markerTransform?.kind === 'marker_to_reference') && !markerFailed;
  $: comparing = selected && preview && preview !== panel.selected_exposure_id;
  $: capture = comparing ? preview : panel.selected_exposure_id;

  let blotWidth = 0, blotNaturalHeight = 0, blotHeight = 0;
  let markerWidth = 0, markerNaturalHeight = 0;
  // Blot and marker share one pixel scale, so their natural widths give the width ratio.
  $: widthRatio = blotWidth && markerWidth ? markerWidth / blotWidth : 0.16;

  $: blotPct = (hasMarker ? Math.min(74, 96 / (1 + widthRatio)) : 74) * widthScale;

  let draft = panel.label || '', focused = false;
  $: syncLabel(panel.label || '');
  function syncLabel(server) { if (!focused) draft = server; }
  $: onediting(panel.panel_id, focused && draft !== (panel.label || ''));
  function commit() {
    const label = draft.trim();
    if (!label) { draft = panel.label || ''; return; }
    if (label !== (panel.label || '')) onrename(panel.panel_id, label);
  }
  async function changeIncluded(event) {
    const input = event.currentTarget, checked = input.checked;
    if (!await onsetincluded(panel.panel_id, checked)) input.checked = !checked;
  }
  function key(event) {
    if (event.key === 'Enter') event.currentTarget.blur();
    else if (event.key === 'Escape') { draft = panel.label || ''; event.currentTarget.blur(); }
  }

  // Lane guide overlay: fractions inside the blot image, resynced whenever the
  // saved layout changes. Internal boundaries can be dragged or arrow-keyed.
  let blotEl = null, edges = [], edgeDrag = null, edgeSyncKey = '';
  $: laneCount = dg?.lane_layout?.lane_count ?? 0;
  $: spans = dg?.lane_layout?.spans || [];
  $: savedEdges = spans.length ? [...new Set(spans.flat())].sort((a, b) => a - b) : laneCount > 0
    ? (dg.lane_layout.boundaries?.length === laneCount + 1
        ? dg.lane_layout.boundaries
        : Array.from({length: laneCount + 1}, (_, i) => i / laneCount))
    : [];
  $: edgeKey = `${panel.panel_id}:${dg?.geometry_revision ?? 0}:${project.project_revision}:${laneCount}`;
  $: if (!edgeDrag && edgeKey !== edgeSyncKey) { edgeSyncKey = edgeKey; edges = savedEdges.slice(); }

  $: minGap = Math.min(0.02, 0.5 / Math.max(laneCount, 1));
  const edgeFraction = event => {
    const box = blotEl.getBoundingClientRect();
    return box.width ? (event.clientX - box.left) / box.width : 0;
  };
  function startEdge(i, event) {
    if (readonly || busy || event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    edgeDrag = {i, moved: false};
  }
  function moveEdge(event) {
    if (!edgeDrag) return;
    const i = edgeDrag.i;
    const x = Math.min(Math.max(edgeFraction(event), edges[i - 1] + minGap), edges[i + 1] - minGap);
    if (x !== edges[i]) { edges = edges.map((v, j) => j === i ? x : v); edgeDrag.moved = true; }
  }
  function endEdge() {
    if (!edgeDrag) return;
    const finished = edgeDrag;
    edgeDrag = null;
    if (finished.moved && onlaneedges) onlaneedges(panel.panel_id, edges.map(Number));
  }
  function cancelEdge() {
    if (!edgeDrag) return;
    edgeDrag = null;
    edges = savedEdges.slice();
  }
  function edgeKeydown(i, event) {
    if (event.key === 'Escape' && edgeDrag) { cancelEdge(); return; }
    const step = event.key === 'ArrowLeft' ? -0.01 : event.key === 'ArrowRight' ? 0.01 : 0;
    if (!step || readonly || busy || !onlaneedges) return;
    event.preventDefault();
    event.stopPropagation();
    const x = Math.min(Math.max(edges[i] + step, edges[i - 1] + minGap), edges[i + 1] - minGap);
    if (x !== edges[i]) { edges = edges.map((v, j) => j === i ? x : v); onlaneedges(panel.panel_id, edges.map(Number)); }
  }
</script>

<svelte:window on:keydown={event => { if (event.key === 'Escape') cancelEdge(); }}/>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="row" class:selected class:readonly data-panel={panel.panel_id} on:click={() => !busy && onselect(panel.panel_id)}>
  <div class="controls">
    <div class="line">
      <span class="grip" class:off={readonly} title="ドラッグで並べ替え" aria-label="並べ替え" role="img"><Icon name="grip"/></span>
      <span class="selection-prefix">Selection</span>
      <input class="inline-label" aria-label="ラベル" title={draft} bind:value={draft} disabled={readonly} on:focus={() => focused = true}
        on:blur={() => { focused = false; commit(); }} on:keydown={key}>
    </div>
    <div class="icons">
      <button class="icon" aria-label="上へ" title="上へ" disabled={busy || readonly || !canUp} on:click|stopPropagation={() => onmove(panel.panel_id, -1)}><Icon name="up"/></button>
      <button class="icon" aria-label="下へ" title="下へ" disabled={busy || readonly || !canDown} on:click|stopPropagation={() => onmove(panel.panel_id, 1)}><Icon name="down"/></button>
      <input type="checkbox" class="include-check" aria-label={`Selection ${panel.label} を図に含める`} checked={panel.included !== false} disabled={busy || readonly}
        on:click|stopPropagation on:change={changeIncluded}>
      <button class="icon" aria-label="Selectionを複製" title="Selectionを複製" disabled={busy || readonly} on:click|stopPropagation={() => onadd(panel.panel_id)}><Icon name="duplicate"/></button>
      <button class="icon" aria-label="Selectionを削除" title="Selectionを削除" disabled={busy || readonly} on:click|stopPropagation={() => ondelete(panel.panel_id)}><Icon name="close"/></button>
    </div>
  </div>
  <div class="visual">
    <div class="blot" bind:this={blotEl} style={`flex:0 0 ${blotPct.toFixed(2)}%`}>
      {#if dg?.crop_rect && capture}
        <img src={panelUrl(panel.panel_id, capture, dg.geometry_revision)} alt={`${panel.label || ''} のブロット`} data-capture={capture} draggable="false"
          bind:naturalWidth={blotWidth} bind:naturalHeight={blotNaturalHeight} bind:clientHeight={blotHeight}>
        {#if comparing}<span class="compare">比較</span>{/if}
        {#each edges as x, i (i)}
          <div class="lane-edge" data-lane-edge={i} style={`left:${x * 100}%`}></div>
        {/each}
        {#if !readonly && onlaneedges && !spans.length}
          {#each edges.slice(1, edges.length - 1) as _, j (j)}
            <button type="button" class="lane-grip" role="separator" aria-orientation="vertical" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={Math.round(edges[j + 1] * 100)} aria-label={`レーン境界 ${j + 1}`} data-lane-boundary={j + 1}
              style={`left:${edges[j + 1] * 100}%`}
              on:pointerdown={event => startEdge(j + 1, event)} on:pointermove={moveEdge} on:pointerup={endEdge}
              on:pointercancel={cancelEdge} on:keydown={event => edgeKeydown(j + 1, event)}></button>
          {/each}
        {/if}
      {:else}<div class="unset">未設定</div>{/if}
    </div>
    {#if hasMarker}
      <div class="marker" data-testid="marker-preview" title="Colorimetric マーカー">
        <img src={markerUrl(panel.panel_id, markerRev)} alt={`${panel.label || ''} のマーカー`} draggable="false"
          style={blotHeight ? `height:${blotHeight}px;width:auto` : ''}
          bind:naturalWidth={markerWidth} bind:naturalHeight={markerNaturalHeight} on:error={() => markerFailed = true}>
      </div>
    {/if}
  </div>
  <span class="membrane-id" aria-label={`メンブレン ${membraneLabel}`}>{membraneLabel}</span>
</div>

<style>
  .row { display: grid; grid-template-columns: 196px minmax(0, 1fr); column-gap: 12px; row-gap: 4px; padding: 8px; border: 2px solid transparent; border-radius: 8px; cursor: pointer; align-items: start; }
  .row.readonly { cursor: pointer; }
  .row.readonly .controls { opacity: .55; }
  .membrane-id { grid-column: 2; justify-self: end; color: var(--muted); font: var(--text-meta)/16px var(--font-mono); }
  .row.readonly:hover { background: #f5f7f7; }
  .grip.off { cursor: default; opacity: .4; }
  .lane-edge { position: absolute; top: 0; bottom: 0; width: 0; border-left: 1px dashed #f2b24a; pointer-events: none; }
  .lane-grip { position: absolute; top: 0; bottom: 0; width: 9px; margin-left: -4.5px; cursor: ew-resize; touch-action: none; border: 0; background: transparent; padding: 0; border-radius: 0; }
  .lane-grip:hover::after, .lane-grip:focus-visible::after { content: ''; position: absolute; inset: 0 3.5px; background: #f2b24a; border-radius: 2px; }
  .row:hover { background: #f6fafa; }
  .row.selected { border-color: #b6c9c9; background: #f5f7f7; }
  .line { display: flex; align-items: center; gap: 4px; }
  .grip { cursor: grab; color: #8a9aa2; font-size: 16px; padding: 0 2px; user-select: none; touch-action: none; }
  .selection-prefix { font-size: var(--text-meta); color: var(--muted); }
  .inline-label { flex: 1 1 auto; width: 100%; font-weight: 600; font-size: var(--text-label); padding: 4px 6px; border-color: transparent; background: transparent; min-width: 0; text-overflow: ellipsis; }
  .inline-label:hover, .inline-label:focus { border-color: #c5d0d7; background: #fff; }
  .icons { display: flex; gap: 3px; margin-top: 4px; padding-left: 22px; }
  .icon { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; padding: 0; font-size: var(--text-body); line-height: 1; border-radius: 6px; color: #5d717c; }
  .visual { display: flex; gap: 8px; align-items: flex-start; min-width: 0; }
  .blot { position: relative; min-width: 0; }
  .blot img { display: block; width: 100%; height: auto; background: #f3f5f6; }
  .marker { flex: 0 1 auto; min-width: 0; overflow: hidden; border-left: 1px solid #d5e2e6; padding-left: 4px; }
  .marker img { display: block; width: auto; max-width: 100%; height: auto; background: #f3f5f6; }
  .compare { position: absolute; top: 3px; right: 3px; font-size: var(--text-caption); font-weight: 600; line-height: 16px; background: #2c4a63; color: #fff; border-radius: 99px; padding: 0 7px; }
  .unset { border: 1px dashed #c5cdd2; color: var(--muted); font-size: var(--text-meta); text-align: center; padding: 10px; }
</style>
