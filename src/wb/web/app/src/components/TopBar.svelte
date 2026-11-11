<script>
  import { isIncluded } from '../lib/api.js';
  import UpdateNotice from './UpdateNotice.svelte';
  import Icon from './Icon.svelte';
  export let project;
  export let busy = false;
  export let onexport;
  export let canUndo = false;
  export let canRedo = false;
  export let historyBusy = false;
  export let onundo;
  export let onredo;
  $: empty = !project.panels.some(isIncluded);
</script>

<header class="top">
  <div class="left">
    <a class="brand" href="../../" title="トップページ（最近開いたプロジェクト）"><img src="./icon.png" alt="">WBM</a>
    <span class="id" title={project.input_archive?.name || project.project_id}>{project.input_archive?.name || project.project_id}</span>
  </div>
  <div class="right">
    <div class="history" role="group" aria-label="操作履歴">
      <button disabled={historyBusy || !canUndo} aria-keyshortcuts="Control+Z Meta+Z" title="元に戻す（Ctrl / ⌘ + Z）" on:click={onundo}><Icon name="undo"/>元に戻す</button>
      <button disabled={historyBusy || !canRedo} aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z Control+Y" title="やり直す（Ctrl / ⌘ + Shift + Z）" on:click={onredo}><Icon name="redo"/>やり直す</button>
    </div>
    <button disabled={busy || empty} on:click={() => onexport('pptx')}>PPTX</button>
    <button disabled={busy || empty} on:click={() => onexport('png')}>PNG</button>
  </div>
  <UpdateNotice/>
</header>

<style>
  .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 16px; background: #fff; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .left, .right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .brand { display: inline-flex; align-items: center; gap: 6px; color: var(--teal); font-size: var(--text-title); font-weight: 600; text-decoration: none; }
  .brand img { width: 24px; height: 24px; }
  .brand:hover { text-decoration: underline; }
  .history { display: flex; gap: 4px; margin-right: 8px; }
  .history button { display: inline-flex; align-items: center; gap: 5px; }
  .id { font-size: var(--text-meta); color: var(--secondary); max-width: 30ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
