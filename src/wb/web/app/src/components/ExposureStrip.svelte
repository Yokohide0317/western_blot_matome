<script>
  import { tick } from 'svelte';
  import Icon from './Icon.svelte';
  import { panelUrl } from '../lib/api.js';
  import { redPixelsForCapture } from '../lib/text.js';
  export let project;
  export let panel;
  export let preview = null;
  export let onpreview;
  export let onadopt;
  export let busy = false;
  let strip, revealed = '';

  $: region = project.membrane_regions.find(r => r.region_id === panel?.region_id);
  $: imageSet = project.image_sets.find(s => s.image_set_id === (panel?.image_set_id || region?.image_set_id));
  $: captures = panel ? project.captures.filter(c => imageSet?.capture_ids.includes(c.capture_id)) : [];
  $: current = preview ?? panel?.selected_exposure_id;
  // Scroll to the adopted exposure whenever another panel (or a new adoption) comes into view.
  $: reveal(`${panel?.panel_id}:${panel?.selected_exposure_id}:${captures.length}`);
  async function reveal(key) {
    if (key === revealed) return;
    revealed = key;
    await tick();
    const card = strip && [...strip.children].find(node => node.dataset.capture === panel?.selected_exposure_id);
    if (card) strip.scrollLeft = card.offsetLeft - strip.offsetLeft - (strip.clientWidth - card.offsetWidth) / 2;
  }
  const seconds = c => c.exposure_s != null ? `${Number(c.exposure_s)}s` : c.capture_id;
</script>

<div bind:this={strip} class="exposure-strip" aria-label="露光">
  {#if panel}{#each captures as c (c.capture_id)}
    <article class:adopted={panel.selected_exposure_id === c.capture_id} class:previewing={current === c.capture_id} data-capture={c.capture_id}>
      <button class="thumb" aria-label={`露光 ${c.capture_id} を比較`} on:click={() => onpreview(c.capture_id)} on:dblclick={() => onadopt(c.capture_id)}>
        {#if panel.display_geometry?.crop_rect}<img loading="lazy" src={panelUrl(panel.panel_id, c.capture_id, panel.display_geometry.geometry_revision)} alt={seconds(c)}>{/if}
      </button>
      <div class="meta">
        <strong>{seconds(c)}</strong>
        {#if redPixelsForCapture(project, panel, c)}<span class="sat-dot" title="飽和画素あり">●</span>{/if}
        <button class="adopt" aria-label={`露光 ${c.capture_id} を採用`} aria-pressed={panel.selected_exposure_id === c.capture_id} disabled={busy}
          title="採用" on:click={() => onadopt(c.capture_id)}><Icon name="check"/></button>
      </div>
    </article>
  {/each}{/if}
</div>

<style>
  .exposure-strip { display: flex; gap: 8px; overflow-x: auto; padding: 8px 12px; background: #fff; border-top: 1px solid var(--border); min-height: 92px; align-items: stretch; position: relative; }
  article { flex: 0 0 152px; border: 1px solid var(--border); border-radius: 8px; padding: 4px; background: #fafbfb; }
  article.previewing { outline: 1px solid var(--teal); outline-offset: 1px; }
  article.adopted { border-color: var(--teal); background: #f4f6f6; }
  .thumb { display: block; width: 100%; height: 40px; padding: 0; border: 0; background: #eef1f2; border-radius: 5px; overflow: hidden; }
  .thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .meta { display: flex; align-items: center; gap: 8px; padding: 4px 0 0; font-size: 12px; }
  .sat-dot { color: #d83a2e; font-size: 11px; line-height: 1; }
  .meta strong { font-weight: 500; }
  .adopt { display: inline-flex; align-items: center; justify-content: center; margin-left: auto; padding: 0 8px; font-size: 12px; line-height: 20px; border-radius: 4px; color: #7b8e97; }
  .adopt[aria-pressed='true'] { background: var(--teal); border-color: var(--teal); color: #fff; }
</style>
