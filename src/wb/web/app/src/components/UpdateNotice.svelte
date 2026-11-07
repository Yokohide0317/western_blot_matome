<script>
  import { onMount } from 'svelte';
  import { dismiss, dismissedVersion, shouldShow, storage, waitForVersion } from '../lib/update.js';

  // The workbench lives at /p/<id>/; the hub API is two levels up.
  const HUB = '../../api/hub';
  let info = null;
  let updating = false;
  let error = '';

  onMount(async () => {
    try {
      const response = await fetch(`${HUB}/update`);
      if (!response.ok) return;  // Served without the hub (tests): no notice.
      const data = await response.json();
      if (shouldShow(data, dismissedVersion(storage()))) info = data;
    } catch { /* offline or no hub */ }
  });

  function later() {
    dismiss(storage(), info.latest);
    info = null;
  }

  async function start() {
    updating = true;
    error = '';
    try {
      const response = await fetch(`${HUB}/update`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || '更新を始められませんでした');
    } catch (failure) {
      updating = false;
      error = failure.message;
      return;
    }
    const readVersion = async () => (await (await fetch(HUB, {cache: 'no-store'})).json()).version;
    if (await waitForVersion(readVersion, info.latest)) {
      location.reload();
      return;
    }
    updating = false;
    error = `更新を確認できませんでした。しばらくしてからページを再読み込みしてください（記録: ${info.log}）`;
  }
</script>

{#if info}
  <div class="update" role="dialog" aria-label="更新のお知らせ">
    <p class="title">新しいバージョン v{info.latest} があります（現在 v{info.current}）</p>
    {#if updating}
      <p>更新中…（1分ほどかかります）</p>
    {:else}
      {#if !info.can_apply}
        <p>{info.reason}</p>
        <code>{info.command}</code>
      {/if}
      {#if error}<p class="error">{error}</p>{/if}
      <div class="actions">
        {#if info.can_apply && !error}<button class="primary" on:click={start}>更新する</button>{/if}
        <button on:click={later}>あとで</button>
      </div>
    {/if}
  </div>
{/if}

<style>
  .update { position: fixed; top: 52px; right: 16px; z-index: 50; width: min(380px, calc(100vw - 32px)); padding: 12px 14px;
    background: #fff; border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 6px 24px rgba(0, 0, 0, .12); }
  p { margin: 0 0 8px; }
  .title { font-weight: 600; color: var(--teal); }
  .error { color: #b4553a; }
  code { display: block; margin-bottom: 8px; padding: 6px 8px; font-size: 11px; background: #f4f6f6; border-radius: 4px; word-break: break-all; user-select: all; }
  .actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
