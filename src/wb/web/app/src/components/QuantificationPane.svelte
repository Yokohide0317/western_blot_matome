<script>
  import { groupTitle, laneName, overlapIssues, statusLabel, statusMark, formatNumber, polarityNames } from '../lib/quantification.js';
  export let project;
  export let panels = [];
  export let busy = false;
  export let pending = false;       // lane edits not yet saved
  export let panelId = '';
  export let groupId = '';
  export let selectedLaneId = null;
  export let group = null;
  export let lanes = [];
  export let info = null;
  export let infoError = '';
  export let onstart;               // (panelId, captureId)
  export let onsettings;            // patch for update_quantification
  export let onlane;                // (laneId, change, immediate)
  export let oncompute;
  export let onplace;
  export let csvUrl = '';
  export let excelUrl = '';

  const csvName = 'quantification.csv';
  $: panel = panels.find(p => p.panel_id === panelId);
  $: groups = (project.quantification_groups || []).filter(g => g.panel_id === panelId);
  $: imageSet = project.image_sets.find(s => s.image_set_id === panel?.image_set_id)
    || project.image_sets.find(s => s.image_set_id === project.membrane_regions.find(r => r.region_id === panel?.region_id)?.image_set_id);
  $: rawSources = new Set(project.sources.filter(s => s.representation_kind === 'analysis_export_candidate' && s.relative_path && s.quant_eligibility !== 'presentation_only' && s.image?.dtype === 'uint16' && ['gray', 'grayscale', 'grey'].includes(s.image?.channels?.toLowerCase())).map(s => s.source_asset_id));
  $: captures = project.captures.filter(c => (imageSet ? imageSet.capture_ids.includes(c.capture_id) : c.capture_id === panel?.selected_exposure_id) && rawSources.has(c.analysis_source_asset_id));
  let captureId = '';
  $: if (!captures.some(c => c.capture_id === captureId)) captureId = captures.some(c => c.capture_id === panel?.selected_exposure_id) ? panel.selected_exposure_id : captures[0]?.capture_id || '';
  const seconds = c => c.exposure_s != null ? `${Number(c.exposure_s)}s` : c.capture_id;
  const panelName = p => p.label || project.membrane_regions.find(r => r.region_id === p.region_id)?.user_label || p.panel_id;
  const countFor = id => (project.quantification_groups || []).filter(g => g.panel_id === id).length;
  const numberOf = g => (project.quantification_groups || []).filter(o => o.panel_id === g.panel_id && o.capture_id === g.capture_id)
    .findIndex(o => o.group_id === g.group_id) + 1;

  // ROI saves must not discard unfinished calculation settings.
  let polarity = 'high', saturation = '', formKey = '', saturationError = '';
  $: savedKey = group ? JSON.stringify([group.group_id, group.signal_polarity, group.saturation_value]) : '';
  $: if (group && savedKey !== formKey) {
    formKey = savedKey;
    polarity = group.signal_polarity || 'high';
    saturation = group.saturation_value == null ? '' : String(group.saturation_value);
    saturationError = '';
  }
  $: savedSaturation = group?.saturation_value == null ? '' : String(group.saturation_value);
  $: formChanged = !!group && (polarity !== group.signal_polarity || saturation.trim() !== savedSaturation);
  $: size = {width: group?.width_px ?? 0, height: group?.height_px ?? 0};
  $: issues = overlapIssues(lanes, size);
  $: results = group?.results;
  $: rawAvailable = !!group && rawSources.has(group.source_asset_id) && project.captures.some(c => c.capture_id === group.capture_id && c.analysis_source_asset_id === group.source_asset_id);
  $: sourceReady = rawAvailable && info?.width > 0 && info?.height > 0 && !infoError;
  $: current = sourceReady && !!results && info?.results_current === true && !pending && !busy && !formChanged;
  $: canCompute = sourceReady && !formChanged && !busy && !pending;

  function saveSettings() {
    const text = saturation.trim();
    const value = text === '' ? null : /^\d+$/.test(text) ? Number(text) : NaN;
    if (Number.isNaN(value) || (value !== null && value > 65535)) { saturationError = '0〜65535の整数で入力してください'; return; }
    saturationError = '';
    onsettings({signal_polarity: polarity, saturation_value: value}, '計算設定を保存しました');
  }
</script>

<div class="quant" data-testid="quant-pane">
  <p class="note">raw16元画像で枠を合わせ、背景を差し引いて定量します。枠は全レーン同じサイズです。</p>

  <section aria-label="段の選択">
    <h2>段</h2>
    {#if panels.length}
      <div class="rows" role="group" aria-label="定量する段">
        {#each panels as p (p.panel_id)}
          <button class="row" class:active={p.panel_id === panelId} aria-pressed={p.panel_id === panelId} disabled={busy || pending}
            data-panel={p.panel_id} on:click={() => { panelId = p.panel_id; groupId = ''; }}>
            <span class="name">{panelName(p)}</span>
            <span class="meta">{project.membrane_regions.find(r => r.region_id === p.region_id)?.user_label || ''}</span>
            {#if countFor(p.panel_id)}<span class="count" title="定量グループ数">定量 {countFor(p.panel_id)}</span>{/if}
          </button>
        {/each}
      </div>
    {:else}<p class="empty-note">「2. バンド選択」で段とレーン数を設定してください。</p>{/if}
    {#if panel}
      <div class="start">
        <label>露光
          <select bind:value={captureId} disabled={busy || pending || !captures.length} aria-label="定量に使う露光">
            {#each captures as c (c.capture_id)}
              <option value={c.capture_id}>{seconds(c)}{c.capture_id === panel.selected_exposure_id ? '（図の採用露光）' : ''}</option>
            {/each}
          </select>
        </label>
        <button class="primary" disabled={busy || pending || !captureId} on:click={() => onstart(panel.panel_id, captureId)}>
          {groups.length ? '定量グループを追加' : '定量を開始'}
        </button>
      </div>
    {/if}
    {#if panel && !captures.length}<p class="warn" role="status" data-testid="quant-no-raw">raw16元画像がないため定量できません。raw16 TIFFを取り込んでください。</p>{/if}
    {#if groups.length}
      <div class="groups" role="group" aria-label="定量グループ">
        {#each groups as g (g.group_id)}
          <button class="chip" class:active={g.group_id === groupId} aria-pressed={g.group_id === groupId} disabled={busy || pending}
            data-group={g.group_id} on:click={() => groupId = g.group_id}>
            {groupTitle(g, project, numberOf(g))}
            <span class="state">{g.results ? '計算済み' : '未計算'}</span>
          </button>
        {/each}
      </div>
    {/if}
  </section>

  {#if group}
    {#if group.capture_id !== panel?.selected_exposure_id}
      <p class="warn" role="note">▲ 図の採用露光とは別の露光を定量します。図の露光は変わりません。</p>
    {/if}
    {#if infoError}<p class="warn" role="alert">▲ {infoError}</p>{/if}
    {#if !rawAvailable && captures.length}<p class="warn" role="status">この定量グループにはraw16元画像がないため定量できません。raw16 TIFFを取り込んでください。</p>{/if}
    {#if group.placement_note}<p class="help" role="status" data-testid="quant-placement">{group.placement_note}</p>{/if}

    <section aria-label="結果">
      <h2>結果</h2>
      <div class="actions">
        <button class="primary" disabled={!canCompute} on:click={oncompute}>計算</button>
        {#if current}
          <a class="button" href={excelUrl} download="quantification.xlsx" data-testid="quant-excel">Excel</a>
          <a class="button" href={csvUrl} download={csvName} data-testid="quant-csv">CSV</a>
        {:else}<button disabled title="計算してから出力できます">Excel</button><button disabled title="計算してから出力できます">CSV</button>{/if}
        {#if pending}<span class="help" role="status">位置を保存中</span>{/if}
        {#if formChanged}<span class="help" role="status">計算設定を保存してください。</span>{/if}
      </div>
      {#if !results}
        <p class="empty-note" data-testid="quant-results-empty">未計算です。</p>
      {:else if !current}
        <p class="warn" role="alert" data-testid="quant-stale">▲ 設定や元画像が変わったため、再計算が必要です。{info?.reason ? `（${info.reason}）` : ''}</p>
      {/if}
      <div class="table-wrap">
        <table class="results" data-testid="quant-results">
          <thead><tr><th scope="col">レーン</th><th scope="col">サンプル</th><th scope="col" class="r">積分強度</th>
            <th scope="col" class="r">背景平均</th><th scope="col" class="r">補正後</th><th scope="col" class="r">飽和画素</th><th scope="col">状態</th><th scope="col">除外</th></tr></thead>
          <tbody>
            {#each lanes as lane, i (lane.lane_id)}
              {@const row = current ? results.rows.find(r => r.lane_id === lane.lane_id) : null}
              {@const name = laneName(group, project, lane.lane_id, i)}
              <tr data-lane={lane.lane_id} class:selected={lane.lane_id === selectedLaneId} class:excluded={lane.excluded}>
                <th scope="row"><button class="lane" aria-pressed={lane.lane_id === selectedLaneId} on:click={() => selectedLaneId = lane.lane_id}>{lane.lane_id}</button></th>
                <td class="sample" title={name}>{name || '—'}</td>
                <td class="r">{formatNumber(row?.raw_sum, 1)}</td>
                <td class="r">{formatNumber(row?.background_mean, 2)}</td>
                <td class="r net">{formatNumber(row?.net_signal, 1)}</td>
                <td class="r">{formatNumber(row?.saturated_pixels)}</td>
                <td>{#if issues[lane.lane_id]}<span class="mark" title={issues[lane.lane_id].join('、')}>▲ {issues[lane.lane_id].join('、')}</span>
                  {:else if row}<span class="mark" aria-hidden="true">{statusMark(row)}</span> {statusLabel(row)}
                  {:else}{lane.excluded ? '除外' : '—'}{/if}</td>
                <td class="flag"><input type="checkbox" checked={lane.excluded} disabled={busy || pending} aria-label={`レーン ${lane.lane_id} を除外`}
                  on:change={event => onlane(lane.lane_id, l => ({...l, excluded: event.currentTarget.checked}), true)}></td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if current}<p class="help">補正後は背景を差し引いた値です。正規化はしていません。</p>{/if}
    </section>

    <details class="settings">
      <summary>計算設定</summary>
      <button disabled={!sourceReady || busy || pending} on:click={onplace}>バンド選択から枠を再配置</button>
      <p class="help">現在の枠と計算結果を置き換えます。</p>
      <fieldset class="polarity" disabled={busy}>
        <legend>信号の向き</legend>
        {#each Object.entries(polarityNames) as [value, name]}
          <label class="radio"><input type="radio" name="quant-polarity" {value} bind:group={polarity}> {name}</label>
        {/each}
      </fieldset>
      <label class="field">飽和しきい値（任意）
        <input class="num" inputmode="numeric" placeholder={polarity === 'high' ? '例: 65535' : '例: 0'} bind:value={saturation} disabled={busy} aria-describedby="sat-help" aria-invalid={!!saturationError}>
      </label>
      <p class="help" id="sat-help">{polarity === 'high' ? 'この値以上' : 'この値以下'}の画素を含むバンド・背景は「飽和」と表示します。空欄では{polarity === 'high' ? '65535' : '0'}で判定しますが、装置の飽和限界は未確認として扱います。</p>
      {#if saturationError}<p class="error" role="alert">{saturationError}</p>{/if}
      <div class="actions">
        <button disabled={busy || pending || !formChanged} on:click={saveSettings}>設定を保存</button>
      </div>
    </details>
  {/if}
</div>

<style>
  .quant { padding: 12px 16px 24px; overflow: auto; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
  section, .settings { border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 8px 12px 12px; }
  h2 { margin: 0 0 6px; font-size: var(--text-body); font-weight: 600; color: #33474f; display: flex; align-items: center; gap: 8px; }
  .note, .help, .empty-note { font-size: var(--text-meta); color: var(--muted); margin: 0; text-wrap: pretty; }
  .help { margin-top: 4px; }
  .warn { margin: 0; font-size: var(--text-meta); color: #9a3412; background: #fff6ed; border: 1px solid #f3c9a5; border-radius: 6px; padding: 6px 10px; }
  .error { margin: 4px 0 0; font-size: var(--text-meta); color: #a33b32; font-weight: 600; }
  .rows { display: flex; flex-direction: column; gap: 4px; max-height: 188px; overflow: auto; }
  .row { display: flex; align-items: center; gap: 8px; text-align: left; padding: 5px 10px; border-radius: 6px; }
  .row.active, .chip.active { border-color: var(--teal); background: #f1f6f6; }
  .row .name { font-weight: 600; flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .meta { color: var(--muted); font-size: var(--text-meta); }
  .count { margin-left: auto; font-size: var(--text-meta); color: var(--teal); white-space: nowrap; }
  .start, .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  .groups { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .chip { display: inline-flex; align-items: center; gap: 8px; padding: 3px 10px; border-radius: 99px; font-size: var(--text-meta); }
  .chip .state { color: var(--muted); }
  select { font: inherit; border: 1px solid #c5d0d7; border-radius: 5px; padding: 4px 8px; background: #fff; color: #263c49; }
  label { font-size: var(--text-meta); color: var(--secondary); }
  .field { display: inline-flex; align-items: center; gap: 6px; }
  .field .num { width: 96px; }
  .num { width: 64px; padding: 2px 6px; text-align: right; font-size: var(--text-meta); }
  input[aria-invalid='true'] { border-color: #a33b32; }
  summary { cursor: pointer; font-size: var(--text-meta); color: var(--secondary); }
  .settings[open] summary { margin-bottom: 10px; }
  .polarity { border: 0; margin: 0 0 8px; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
  .polarity legend { font-size: var(--text-meta); color: var(--secondary); padding: 0; margin-bottom: 2px; }
  .radio { display: inline-flex; align-items: center; gap: 6px; color: #263c49; font-size: var(--text-body); }
  .radio input, td.flag input { width: auto; accent-color: var(--teal); margin: 0; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: var(--text-meta); }
  th, td { padding: 3px 6px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; font-weight: 400; }
  thead th { color: var(--muted); font-weight: 500; }
  .r { text-align: right; }
  td.sample { max-width: 14ch; overflow: hidden; text-overflow: ellipsis; }
  td.flag { text-align: center; }
  tr.selected { background: #f1f4f4; }
  tr.excluded td { color: var(--muted); }
  .lane { min-width: 28px; padding: 1px 6px; font-size: var(--text-meta); border-radius: 5px; }
  .lane[aria-pressed='true'] { background: var(--teal); border-color: var(--teal); color: #fff; }
  .mark { font-weight: 700; }
  .net { font-weight: 600; }
  a.button { display: inline-block; border: 1px solid #cbd6dc; background: #fff; color: #263c49; border-radius: 4px; padding: 4px 12px; font-weight: 500; text-decoration: none; }
  a.button:hover { background: #f1f3f3; border-color: #9ba9ab; }
  a.button:focus-visible { outline: 2px solid #70bdc1; outline-offset: 1px; }
</style>
