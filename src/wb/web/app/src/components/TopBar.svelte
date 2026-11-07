<script>
  import { isIncluded } from '../lib/api.js';
  import UpdateNotice from './UpdateNotice.svelte';
  export let project;
  export let busy = false;
  export let onexport;
  $: empty = !project.panels.some(isIncluded);
</script>

<header class="top">
  <div class="left">
    <a class="brand" href="../../" title="トップページ（最近開いたプロジェクト）">WBM</a>
    <span class="id" title={project.input_archive?.name || project.project_id}>{project.input_archive?.name || project.project_id}</span>
  </div>
  <div class="right">
    <button disabled={busy || empty} on:click={() => onexport('pptx')}>PPTX</button>
    <button disabled={busy || empty} on:click={() => onexport('png')}>PNG</button>
  </div>
  <UpdateNotice/>
</header>

<style>
  .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 16px; background: #fff; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .left, .right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .brand { color: var(--teal); font-size: 16px; font-weight: 600; text-decoration: none; }
  .brand:hover { text-decoration: underline; }
  .id { font-size: 12px; color: var(--secondary); max-width: 30ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
