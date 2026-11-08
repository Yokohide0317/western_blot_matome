<script>
  import { zoomedView } from '../lib/editing.js';
  import { arrowDelta, overlapIssues, formatNumber, resizeFromDrag } from '../lib/quantification.js';
  import Icon from './Icon.svelte';
  // Raw-pixel view of one quantification group. Every coordinate here is a full-resolution raw pixel.
  export let group = null;
  export let image = null;       // {width, height} from the server, never from the browser
  export let imageUrl = '';
  export let lanes = [];
  export let selectedLaneId = null;
  export let loadError = '';
  export let onselect;
  export let onedit;             // (laneId, kind, x, y) while dragging
  export let onnudge;            // (laneId, kind, dx, dy) from the keyboard
  export let oncommit;
  export let ondrag;
  export let onresize;
  export let busy = false;
  let resize = null, resizePreview = null;
  const handles = [[-1,-1], [0,-1], [1,-1], [-1,0], [1,0], [-1,1], [0,1], [1,1]];

  const MAX_ZOOM = 40;
  let svg, vb = null, viewerWidth = 1, viewerHeight = 1, drag = null, pan = null, resetKey = '', broken = false;

  $: baseSize = {width: group?.width_px ?? 1, height: group?.height_px ?? 1};
  $: size = resizePreview?.size || baseSize;
  $: visibleLanes = resizePreview?.lanes || lanes;
  $: whole = {x: 0, y: 0, width: image?.width || 1, height: image?.height || 1};
  $: view = vb || whole;
  $: scale = Math.min(viewerWidth / view.width, viewerHeight / view.height) || 1;
  $: zoom = whole.width / view.width;
  $: issues = overlapIssues(visibleLanes, size);
  $: selectedLane = visibleLanes.find(l => l.lane_id === selectedLaneId);
  $: if (`${group?.group_id}:${image?.width}x${image?.height}` !== resetKey) { resetKey = `${group?.group_id}:${image?.width}x${image?.height}`; vb = null; broken = false; }
  $: if (imageUrl) broken = false;

  function svgPoint(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const mapped = point.matrixTransform(svg.getScreenCTM().inverse());
    return [mapped.x, mapped.y];
  }
  function wheelZoom(node) {
    const handler = event => {
      event.preventDefault();
      vb = zoomedView(view, whole, svgPoint(event), Math.exp(-event.deltaY * 0.0018), MAX_ZOOM);
    };
    node.addEventListener('wheel', handler, {passive: false});
    return {destroy: () => node.removeEventListener('wheel', handler)};
  }
  function zoomBy(factor) {
    vb = zoomedView(view, whole, [view.x + view.width / 2, view.y + view.height / 2], factor, MAX_ZOOM);
  }
  function focusLane() {
    if (!selectedLane || !image) return;
    const xs = [selectedLane.x, selectedLane.background_x], ys = [selectedLane.y, selectedLane.background_y];
    const margin = Math.max(size.width, size.height) * 1.5;
    const x0 = Math.max(0, Math.min(...xs) - margin), x1 = Math.min(image.width, Math.max(...xs) + size.width + margin);
    const y0 = Math.max(0, Math.min(...ys) - margin), y1 = Math.min(image.height, Math.max(...ys) + size.height + margin);
    const aspect = viewerWidth / viewerHeight;
    let width = Math.max(x1 - x0, (y1 - y0) * aspect), height = width / aspect;
    width = Math.min(width, whole.width); height = Math.min(height, whole.height);
    vb = width >= whole.width - 1e-9 ? null : {x: Math.min(Math.max(0, (x0 + x1) / 2 - width / 2), whole.width - width),
      y: Math.min(Math.max(0, (y0 + y1) / 2 - height / 2), whole.height - height), width, height};
  }

  // Dragging a box: it follows the pointer by whole raw pixels and is clamped by the owner.
  function startBox(event, lane, kind) {
    if (event.button !== 0 || resize || busy) return;
    event.preventDefault(); event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus({preventScroll: true});
    onselect(lane.lane_id);
    drag = {laneId: lane.lane_id, kind, from: svgPoint(event), x: kind === 'band' ? lane.x : lane.background_x, y: kind === 'band' ? lane.y : lane.background_y, moved: false};
    ondrag(true);
  }
  function moveBox(event) {
    if (!drag) return;
    const [px, py] = svgPoint(event);
    drag.moved = true;
    onedit(drag.laneId, drag.kind, drag.x + (px - drag.from[0]), drag.y + (py - drag.from[1]));
  }
  function endBox() {
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    ondrag(false);
    if (moved) oncommit();
  }
  function keyBox(event, lane, kind) {
    if (resize || busy) return;
    const delta = arrowDelta(event);
    if (!delta) return;
    event.preventDefault();
    onnudge(lane.lane_id, kind, delta[0], delta[1]);
  }

  function startResize(event, horizontal, vertical) {
    if (event.button !== 0 || busy || resize) return;
    event.preventDefault(); event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus({preventScroll: true});
    resize = {horizontal, vertical, from: svgPoint(event), size: {...baseSize}, lanes: lanes.map(l => ({...l}))};
    ondrag(true);
  }
  function moveResize(event) {
    if (!resize) return;
    event.stopPropagation();
    const [x,y] = svgPoint(event);
    resizePreview = resizeFromDrag(resize.lanes, resize.size, image, resize.horizontal, resize.vertical, x-resize.from[0], y-resize.from[1]);
  }
  async function endResize(event) {
    if (!resize) return;
    event?.stopPropagation();
    const target = resizePreview?.size;
    const before = resize.size;
    resize = null;
    ondrag(false);
    try {
      if (target && (target.width !== before.width || target.height !== before.height)) {
        await onresize({width_px: target.width, height_px: target.height});
      }
    } finally { resizePreview = null; }
  }
  function keyResize(event, horizontal, vertical) {
    event.stopPropagation();
    if (busy || resize) return;
    const delta = arrowDelta(event);
    if (!delta) return;
    event.preventDefault();
    const next = resizeFromDrag(lanes, baseSize, image, horizontal, vertical, ...delta).size;
    if (next.width !== baseSize.width || next.height !== baseSize.height) onresize({width_px: next.width, height_px: next.height});
  }

  function startPan(event) {
    if (event.button !== 0 || !vb) return;
    svg.setPointerCapture(event.pointerId);
    pan = {x: event.clientX, y: event.clientY, view: {...view}};
  }
  function movePan(event) {
    if (!pan) return;
    const x = pan.view.x - (event.clientX - pan.x) / scale, y = pan.view.y - (event.clientY - pan.y) / scale;
    vb = {...pan.view, x: Math.min(Math.max(0, x), whole.width - pan.view.width), y: Math.min(Math.max(0, y), whole.height - pan.view.height)};
  }
  const endPan = () => { pan = null; };

  const boxName = (lane, kind) => `レーン ${lane.lane_id} ${kind === 'band' ? 'バンド' : '背景'}の枠`;
  const boxValue = (lane, kind) => kind === 'band' ? `x ${lane.x}, y ${lane.y}` : `x ${lane.background_x}, y ${lane.background_y}`;
</script>

<svelte:window on:blur={() => { endBox(); endPan(); endResize(); }}/>

<div class="quant-canvas">
  <div class="bar">
    <div class="seg" role="group" aria-label="拡大">
      <button class="view" aria-label="縮小" title="縮小" disabled={!image || !vb} on:click={() => zoomBy(1 / 1.6)}>−</button>
      <button class="view" aria-label="拡大" title="拡大" disabled={!image} on:click={() => zoomBy(1.6)}><Icon name="plus"/></button>
      <button class="view" aria-label="全体" title="画像全体を表示" aria-pressed={!vb} disabled={!image} on:click={() => vb = null}><Icon name="zoom-whole"/></button>
      <button class="view" aria-label="選択レーンに寄る" title="選択レーンに寄る" disabled={!selectedLane} on:click={focusLane}><Icon name="zoom-membrane"/></button>
    </div>
    <span class="legend" aria-hidden="true"><i class="swatch band"></i>バンド <i class="swatch bg"></i>背景</span>
    <span class="grow"></span>
    {#if image}<span class="pill" data-testid="quant-image-size">{formatNumber(image.width)} × {formatNumber(image.height)} px · {zoom.toFixed(1)}×</span>{/if}
  </div>
  <div class="stage">
    {#if group && image && !loadError}
      <svg bind:this={svg} class="canvas" class:zoomed={!!vb} class:panning={!!pan} viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`} preserveAspectRatio="xMidYMid meet"
        bind:clientWidth={viewerWidth} bind:clientHeight={viewerHeight} data-zoom={zoom.toFixed(3)} aria-label="定量用の元画像" role="group" use:wheelZoom
        on:pointerdown={startPan} on:pointermove={movePan} on:pointerup={endPan} on:pointercancel={endPan} on:lostpointercapture={endPan} on:dblclick={() => vb = null}>
        <image preserveAspectRatio="none" class:crisp={zoom > 4} href={imageUrl} width={image.width} height={image.height} on:error={() => broken = true}/>
        {#each visibleLanes as lane (lane.lane_id)}
          {#each ['background', 'band'] as kind}
            {@const x = kind === 'band' ? lane.x : lane.background_x}
            {@const y = kind === 'band' ? lane.y : lane.background_y}
            <g class={`roi ${kind}`} class:selected={lane.lane_id === selectedLaneId} class:excluded={lane.excluded} class:warn={!!issues[lane.lane_id]}
              data-lane={lane.lane_id} data-kind={kind} data-x={x} data-y={y}
              role="button" tabindex="0" aria-roledescription="移動できる枠" aria-label={`${boxName(lane, kind)}${lane.excluded ? '（除外）' : ''}、${boxValue(lane, kind)}。矢印キーで移動、Shiftで10px`}
              on:pointerdown={event => startBox(event, lane, kind)} on:pointermove={moveBox} on:pointerup={endBox} on:pointercancel={endBox} on:lostpointercapture={endBox}
              on:keydown={event => keyBox(event, lane, kind)} on:focus={() => onselect(lane.lane_id)}>
              <rect {x} {y} width={size.width} height={size.height} vector-effect="non-scaling-stroke"/>
              {#if lane.lane_id === selectedLaneId}
                {#each handles as [horizontal, vertical]}
                  <rect class="resize-handle" data-resize={`${horizontal},${vertical}`} role="button" tabindex="0"
                    aria-label="全レーンの枠サイズを変更" aria-disabled={busy}
                    x={x + (horizontal+1)*size.width/2 - 5/scale} y={y + (vertical+1)*size.height/2 - 5/scale}
                    width={10/scale} height={10/scale} vector-effect="non-scaling-stroke"
                    style={`cursor: ${!horizontal ? 'ns' : !vertical ? 'ew' : horizontal === vertical ? 'nwse' : 'nesw'}-resize`}
                    on:pointerdown={event => startResize(event, horizontal, vertical)} on:pointermove={moveResize}
                    on:pointerup={endResize} on:pointercancel={endResize} on:lostpointercapture={endResize}
                    on:keydown={event => keyResize(event, horizontal, vertical)}/>
                {/each}
              {/if}
              <text x={x + 3 / scale} y={y - 4 / scale} font-size={12 / scale} pointer-events="none">{lane.lane_id}{kind === 'background' ? ' 背景' : ''}{issues[lane.lane_id] ? ' !' : ''}{lane.excluded ? ' 除外' : ''}</text>
            </g>
          {/each}
        {/each}
      </svg>
    {:else if loadError}<div class="blank" role="alert">{loadError}</div>
    {:else if group}<div class="blank" role="status">画像を読み込み中</div>
    {:else}<div class="blank">定量を開始すると、元画像が表示されます</div>{/if}
    {#if broken}<div class="blank over" role="alert">定量用の画像を表示できません</div>{/if}
  </div>
  {#if selectedLane}
    <div class="readout" aria-live="polite" data-testid="quant-readout">
      <b>レーン {selectedLane.lane_id}</b>
      <span>バンド x {selectedLane.x}, y {selectedLane.y}</span>
      <span>背景 x {selectedLane.background_x}, y {selectedLane.background_y}</span>
      <span>枠 {size.width} × {size.height} px</span>
      {#if issues[selectedLane.lane_id]}<span class="warning">▲ {issues[selectedLane.lane_id].join('、')}</span>{/if}
    </div>
  {/if}
</div>

<style>
  .quant-canvas { display: flex; flex-direction: column; min-height: 0; height: 100%; }
  .bar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; flex-wrap: wrap; border-bottom: 1px solid var(--border); background: #fff; }
  .grow { flex: 1; }
  .pill { font-size: var(--text-meta); background: #eef3f4; border-radius: 99px; padding: 2px 9px; white-space: nowrap; }
  .seg { display: inline-flex; border: 1px solid #c8d3d8; border-radius: 7px; overflow: hidden; }
  .seg button { border: 0; border-radius: 0; padding: 3px 11px; font-size: var(--text-meta); background: #fff; }
  .seg button[aria-pressed='true'] { background: var(--teal); color: #fff; }
  .seg button + button { border-left: 1px solid #c8d3d8; }
  .seg .view { display: inline-flex; align-items: center; justify-content: center; min-width: 38px; height: 26px; }
  .legend { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-meta); color: var(--secondary); }
  .swatch { display: inline-block; width: 14px; height: 10px; margin-left: 6px; }
  .swatch.band { border: 2px solid #36e0d6; }
  .swatch.bg { border: 2px dashed #ffb454; }
  .stage { flex: 1; min-height: 340px; background: #1c2428; position: relative; }
  .canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; user-select: none; cursor: default; }
  .canvas.zoomed { cursor: grab; }
  .canvas.panning { cursor: grabbing; }
  .canvas image.crisp { image-rendering: pixelated; }
  .blank { color: #9fb0b7; display: grid; place-items: center; height: 100%; padding: 16px; text-align: center; font-size: var(--text-body); }
  .blank.over { position: absolute; inset: 0; background: #1c2428e6; }
  .roi { cursor: move; outline: none; }
  .roi rect { fill: transparent; stroke-width: 1.5; pointer-events: all; }
  .roi .resize-handle { fill: #fff; stroke: #193238; stroke-width: 1.5; stroke-dasharray: none; }
  .roi .resize-handle:focus-visible { fill: #ffd23f; outline: none; }
  .roi text { font-weight: 700; paint-order: stroke; stroke: #0d1a1f; stroke-width: 3px; stroke-linejoin: round; }
  .roi.band rect { stroke: #36e0d6; fill: #36e0d610; }
  .roi.band text { fill: #d9fffa; }
  .roi.background rect { stroke: #ffb454; stroke-dasharray: 6 4; fill: #ffb45410; }
  .roi.background text { fill: #ffe6c2; }
  .roi.selected rect { stroke-width: 3; }
  .roi.excluded { opacity: .45; }
  .roi.excluded rect { stroke-dasharray: 2 4; }
  .roi.warn rect { stroke: #ff6b5a; }
  .roi:focus-visible rect { stroke: #ffd23f; stroke-width: 4; stroke-dasharray: none; }
  .readout { display: flex; gap: 6px 14px; flex-wrap: wrap; align-items: baseline; padding: 6px 12px; border-top: 1px solid var(--border); background: #fff; font-size: var(--text-meta); color: var(--secondary); }
  .readout b { color: #193238; }
  .warning { color: #9a3412; font-weight: 600; }
</style>
