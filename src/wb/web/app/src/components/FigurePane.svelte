<script>
  import FigureRow from './FigureRow.svelte';
  import { panelWidthFactors } from '../lib/laneScale.js';
  export let project;
  export let panels;
  export let selectedPanelId;
  export let activeRegionId = null;
  export let preview = null;
  export let busy = false;
  export let onselect;
  export let onrename;
  export let onadd;
  export let onsetincluded;
  export let ondelete;
  export let onreorder;
  export let onlanes;
  export let onsetnames;
  export let onlaneedges;
  export let onediting;

  $: rows = panels.filter(p => project.membrane_regions.some(r => r.region_id === p.region_id && r.included));
  $: widthFactors = panelWidthFactors(project, rows);
  $: locked = new Set(rows.filter(p => activeRegionId && p.region_id !== activeRegionId).map(p => p.panel_id));
  $: hidden = panels.filter(p => !rows.includes(p));
  $: first = rows[0];
  $: selected = rows.find(p => p.panel_id === selectedPanelId) || panels.find(p => p.panel_id === selectedPanelId) || first;
  $: group = project.sample_groups.find(g => g.sample_group_id === selected?.sample_group_id);
  $: region = project.membrane_regions.find(r => r.region_id === selected?.region_id);
  $: laneCount = group?.lane_count ?? selected?.display_geometry?.lane_layout?.lane_count ?? region?.lane_count ?? 0;
  $: serverNames = Array.from({length: laneCount}, (_, i) => group?.samples?.[i]?.name ?? '');
  $: boundaries = selected?.display_geometry?.lane_layout?.boundaries;
  $: spans = selected?.display_geometry?.lane_layout?.spans || [];
  $: widths = boundaries?.length === laneCount + 1
    ? boundaries.slice(1).map((v, i) => `${(v - boundaries[i]) * 100}%`).join(' ')
    : `repeat(${Math.max(laneCount, 1)}, 1fr)`;

  let names = [], focusedLane = -1, pendingNames = {}, namesKey = '';
  $: syncNames(serverNames, `${selected?.panel_id}:${laneCount}`);
  function syncNames(server, key) {
    if (key !== namesKey) { namesKey = key; pendingNames = {}; }
    const unacknowledged = {};
    names = server.map((name, i) => {
      if (i in pendingNames && pendingNames[i] !== name) unacknowledged[i] = pendingNames[i];
      return i === focusedLane ? (names[i] ?? name) : (unacknowledged[i] ?? name);
    });
    pendingNames = unacknowledged;
  }
  $: onediting('lanes', Object.keys(pendingNames).length > 0 || (focusedLane >= 0 && names.some((n, i) => n !== serverNames[i])));
  function setLanes(count) {
    count = Math.max(1, count);
    // A row gets its own group so a shared group's names/count cannot silently
    // change the other membranes. All rows at once is the Master Control's job.
    onlanes(count, selected?.panel_id, Array.from({length: count}, (_, i) => names[i]?.trim() || null));
  }
  function commitNames() {
    if (names.every((n, i) => n === serverNames[i])) return;
    const values = names.map(n => n.trim() || null);
    // Preserve later edits when an earlier save refreshes the whole project.
    pendingNames = Object.fromEntries(values.map((name, i) => [i, name ?? ''])
      .filter(([i, name]) => name !== serverNames[i]));
    if (group) onsetnames(group.sample_group_id, values);
    else onlanes(laneCount, selected?.panel_id, values);
  }
  function laneKey(event) { if (event.key === 'Enter') event.currentTarget.blur(); }

  let dragging = '', over = '';
  function reorderTo(from, to) {
    if (!from || from === to || locked.has(from) || locked.has(to)) return;
    const ids = rows.map(p => p.panel_id);
    if (!ids.includes(from) || !ids.includes(to)) return;
    const target = ids.indexOf(to);
    ids.splice(ids.indexOf(from), 1);
    ids.splice(target, 0, from);
    onreorder([...ids, ...hidden.map(p => p.panel_id)]);
  }
  function startReorder(id, event) {
    if (busy || locked.has(id) || event.button !== 0 || !event.target.closest('.grip')) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragging = id;
    over = id;
  }
  const slotAt = event => document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-slot]')?.dataset.slot || '';
  function moveReorder(event) {
    if (!dragging) return;
    const hit = slotAt(event);
    if (hit) over = hit;
  }
  function endReorder(event) {
    if (!dragging) return;
    const from = dragging, to = slotAt(event) || over;
    dragging = over = '';
    if (to && from !== to) reorderTo(from, to);
  }
  function move(id, step) {
    const ids = rows.map(p => p.panel_id), at = ids.indexOf(id), to = at + step;
    if (to < 0 || to >= ids.length || locked.has(id) || locked.has(ids[to])) return;
    [ids[at], ids[to]] = [ids[to], ids[at]];
    onreorder([...ids, ...hidden.map(p => p.panel_id)]);
  }
</script>

<div class="figure">
  <div class="sheet">
    <div class="rows">
      {#each rows as p, i (p.panel_id)}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="slot" class:over={over === p.panel_id && dragging !== p.panel_id} class:lifted={dragging === p.panel_id}
          data-slot={p.panel_id}
          on:pointerdown={event => startReorder(p.panel_id, event)}
          on:pointermove={moveReorder}
          on:pointerup={endReorder}
          on:pointercancel={() => { dragging = over = ''; }}>
          {#if p.panel_id === selected?.panel_id}
            <div class="lane-editor" data-panel={p.panel_id}>
              <div class="lanes">
                <div class="stepper" role="group" aria-label="レーン数">
                  {#if laneCount}
                    <button class="icon" aria-label="レーンを減らす" title="レーンを減らす" disabled={busy || laneCount <= 1} on:click={() => setLanes(laneCount - 1)}>−</button>
                    <span class="count" title="レーン数">レーン {laneCount}</span>
                    <button class="icon" aria-label="レーンを増やす" title="レーンを増やす" disabled={busy} on:click={() => setLanes(laneCount + 1)}>+</button>
                    {#if spans.length}<span class="lane-note">空きを含むレーン配置</span>{/if}
                  {:else}
                    <button class="add-lane" aria-label="レーンを設定" disabled={busy || !rows.length} on:click={() => setLanes(1)}>レーン +</button>
                  {/if}
                </div>
                <div class="lane-visual">
                  <div class="cells" class:spanned={spans.length > 0} style={`grid-template-columns:${widths};flex-basis:${74 * (widthFactors[selected?.panel_id] ?? 1)}%`}>
                    {#each names as _, i (i)}
                      <input class="lane-name" aria-label={`レーン ${i + 1}`} placeholder={names.some(n => n?.trim()) ? '' : String(i + 1)} bind:value={names[i]}
                        style={spans[i] ? `position:absolute;left:${spans[i][0] * 100}%;width:${(spans[i][1] - spans[i][0]) * 100}%` : ''}
                        on:focus={() => focusedLane = i} on:blur={() => { focusedLane = -1; commitNames(); }} on:keydown={laneKey}>
                    {/each}
                  </div>
                </div>
              </div>
            </div>
          {/if}
          <FigureRow {project} panel={p} selected={p.panel_id === selectedPanelId} readonly={locked.has(p.panel_id)} {preview} {busy}
            widthScale={widthFactors[p.panel_id] ?? 1}
            canUp={i > 0 && !locked.has(rows[i - 1].panel_id)} canDown={i < rows.length - 1 && !locked.has(rows[i + 1].panel_id)} {onselect} {onrename} {onadd} {ondelete} {onsetincluded}
            onmove={move} {onlaneedges} {onediting}/>
        </div>
      {/each}
    </div>
  </div>

</div>

<style>
  .figure { display: flex; flex-direction: column; gap: 12px; padding: 16px; flex: 1; min-height: 0; overflow: auto; }
  .lane-editor { background: #f4f5f5; border-radius: 8px 8px 0 0; }
  .lane-note { font-size: var(--text-meta); color: var(--muted); white-space: nowrap; }
  .sheet { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 8px; }
  .lanes { display: grid; grid-template-columns: 196px minmax(0, 1fr); gap: 12px; padding: 8px; border: 2px solid transparent; border-radius: 8px; align-items: center; }
  .stepper { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; justify-content: flex-start; padding-left: 22px; font-size: var(--text-meta); }
  .count { color: #52666f; white-space: nowrap; }
  .icon { width: 24px; min-width: 24px; height: 24px; padding: 0; line-height: 1; border-radius: 6px; }
  .add-lane { font-size: var(--text-meta); padding: 4px 12px; border-radius: 4px; }
  .lane-visual { display: flex; }
  .cells { flex: 0 0 74%; display: grid; gap: 0; min-width: 0; }
  .cells.spanned { display: block; position: relative; height: 30px; }
  .lane-name { text-align: center; padding: 3px 1px; font-size: var(--text-meta); border-radius: 0; border-color: #d4dcE0; min-width: 0; }
  .rows { display: flex; flex-direction: column; gap: 2px; }
  .slot { border-radius: 8px; }
  .slot.over { box-shadow: 0 -3px 0 var(--teal); }
  .slot.lifted { opacity: .5; }
</style>
