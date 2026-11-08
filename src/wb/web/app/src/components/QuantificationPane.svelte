<script>
  import { resizedLanes, groupTitle, laneName, overlapIssues, statusLabel, statusMark, formatNumber, toInt, polarityNames } from '../lib/quantification.js';
  export let project;
  export let panels = [];
  export let busy = false;
  export let pending = false;       // lane edits not yet saved
  export let panelId = '';
  export let groupId = '';
  export let selectedLaneId = null;
  export let group = null;
  export let lanes = [];
  export let image = null;
  export let info = null;
  export let infoError = '';
  export let onstart;               // (panelId, captureId)
  export let onsettings;            // patch for update_quantification
  export let onlane;                // (laneId, change, immediate)
  export let onsetroi;              // (laneId, kind, x, y)
  export let oncompute;
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

  // Form fields follow the saved group except while they are being typed in.
  let polarity = 'high', saturation = '', widthText = '', heightText = '', acknowledged = false, formKey = '', sizeError = '', saturationError = '';
  $: if (group && `${group.group_id}:${project.project_revision}` !== formKey) {
    const sameGroup = formKey.split(':')[0] === group.group_id;
    formKey = `${group.group_id}:${project.project_revision}`;
    polarity = group.signal_polarity || 'high';
    saturation = group.saturation_value == null ? '' : String(group.saturation_value);
    widthText = String(group.width_px); heightText = String(group.height_px);
    sizeError = saturationError = '';
    if (!sameGroup || group.source_confirmed === true) acknowledged = group.source_confirmed === true;
  }
  $: savedSaturation = group?.saturation_value == null ? '' : String(group.saturation_value);
  $: formChanged = !!group && (polarity !== group.signal_polarity || saturation.trim() !== savedSaturation);
  $: confirmed = group?.source_confirmed === true;
  $: size = {width: group?.width_px ?? 0, height: group?.height_px ?? 0};
  $: issues = overlapIssues(lanes, size);
  $: results = group?.results;
  $: rawAvailable = !!group && rawSources.has(group.source_asset_id) && project.captures.some(c => c.capture_id === group.capture_id && c.analysis_source_asset_id === group.source_asset_id);
  $: sourceReady = rawAvailable && info?.width > 0 && info?.height > 0 && !infoError;
  $: current = sourceReady && !!results && info?.results_current === true && !pending && !busy && !formChanged;
  $: canCompute = sourceReady && confirmed && !formChanged && !busy && !pending;

  function confirmSource() {
    const text = saturation.trim();
    const value = text === '' ? null : /^\d+$/.test(text) ? Number(text) : NaN;
    if (Number.isNaN(value) || (value !== null && value > 65535)) { saturationError = '0〜65535の整数で入力してください'; return; }
    saturationError = '';
    onsettings({signal_polarity: polarity, saturation_value: value, source_confirmed: true}, '元データの条件を確認しました');
  }
  function applySize() {
    const width = toInt(widthText), height = toInt(heightText);
    sizeError = '';
    if (!(width >= 1 && height >= 1)) { sizeError = '1以上の整数で入力してください'; return; }
    if ((width - group.width_px) % 2 || (height - group.height_px) % 2) { sizeError = '中心を保つため、現在の値から2px刻みで変更してください'; return; }
    if (width === group.width_px && height === group.height_px) return;
    if (image && !resizedLanes(lanes, size, {width, height}, image)) { sizeError = '画像の外に出る枠があるため変更できません'; return; }
    onsettings({width_px: width, height_px: height}, '枠のサイズを変更しました');
  }
  const sizeKey = event => { if (event.key === 'Enter') applySize(); };
  function coordinate(lane, kind, axis, event) {
    const value = toInt(event.currentTarget.value);
    const x = kind === 'band' ? lane.x : lane.background_x, y = kind === 'band' ? lane.y : lane.background_y;
    if (value == null) { event.currentTarget.value = axis === 'x' ? x : y; return; }
    onsetroi(lane.lane_id, kind, axis === 'x' ? value : x, axis === 'y' ? value : y);
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
            <span class="state">{g.results ? '計算済み' : g.source_confirmed ? '確認済み' : '未確認'}</span>
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

    <section aria-label="元データの確認">
      <h2>元データの確認 <span class="badge" class:ok={confirmed && !formChanged}>{confirmed && !formChanged ? '✓ 確認済み' : '未確認'}</span></h2>
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
      <label class="check"><input type="checkbox" bind:checked={acknowledged} disabled={busy}> 加工のない線形な元データ（16-bit）であることを確認した</label>
      <div class="actions">
        <button class="primary" disabled={busy || pending || !acknowledged || (confirmed && !formChanged)} on:click={confirmSource}>
          {confirmed && formChanged ? '変更して再確認' : '条件を確認'}
        </button>
        {#if confirmed}<button disabled={busy || pending} on:click={() => onsettings({source_confirmed: false}, '確認を解除しました')}>確認を解除</button>{/if}
      </div>
    </section>

    <section aria-label="枠のサイズ">
      <h2>枠のサイズ（全レーン共通）</h2>
      <div class="size">
        <label class="field">幅 <input class="num" inputmode="numeric" aria-label="枠の幅 (px)" bind:value={widthText} on:keydown={sizeKey} disabled={busy} aria-invalid={!!sizeError}></label>
        <label class="field">高さ <input class="num" inputmode="numeric" aria-label="枠の高さ (px)" bind:value={heightText} on:keydown={sizeKey} disabled={busy} aria-invalid={!!sizeError}></label>
        <span class="unit">px</span>
        <button disabled={busy || pending} on:click={applySize}>適用</button>
      </div>
      <p class="help">バンドと背景の枠は同じサイズです。中心を保って2px刻みで変わります。画像上の角・辺でも変更できます。</p>
      {#if sizeError}<p class="error" role="alert" data-testid="quant-size-error">{sizeError}</p>{/if}
    </section>

    <section aria-label="枠の位置">
      <h2>枠の位置 <span class="unit">元画像のpx</span></h2>
      <div class="table-wrap">
        <table data-testid="quant-lanes">
          <thead><tr><th scope="col">レーン</th><th scope="col">サンプル</th><th scope="col">バンド x, y</th><th scope="col">背景 x, y</th><th scope="col">除外</th></tr></thead>
          <tbody>
            {#each lanes as lane, i (lane.lane_id)}
              {@const name = laneName(group, project, lane.lane_id, i)}
              <tr class:selected={lane.lane_id === selectedLaneId} class:excluded={lane.excluded} data-lane={lane.lane_id}>
                <th scope="row"><button class="lane" aria-pressed={lane.lane_id === selectedLaneId} on:click={() => selectedLaneId = lane.lane_id}>{lane.lane_id}</button></th>
                <td class="sample" title={name}>{name || '—'}</td>
                {#each ['band', 'background'] as kind}
                  <td class="xy">
                    {#if lane.lane_id === selectedLaneId}
                      <input class="num" inputmode="numeric" aria-label={`レーン ${lane.lane_id} ${kind === 'band' ? 'バンド' : '背景'} x`} value={kind === 'band' ? lane.x : lane.background_x} on:change={event => coordinate(lane, kind, 'x', event)}>
                      <input class="num" inputmode="numeric" aria-label={`レーン ${lane.lane_id} ${kind === 'band' ? 'バンド' : '背景'} y`} value={kind === 'band' ? lane.y : lane.background_y} on:change={event => coordinate(lane, kind, 'y', event)}>
                    {:else}{kind === 'band' ? lane.x : lane.background_x}, {kind === 'band' ? lane.y : lane.background_y}{/if}
                  </td>
                {/each}
                <td class="flag">
                  <input type="checkbox" checked={lane.excluded} aria-label={`レーン ${lane.lane_id} を除外`} on:change={event => onlane(lane.lane_id, l => ({...l, excluded: event.currentTarget.checked}), true)}>
                  {#if issues[lane.lane_id]}<span class="mark" title={issues[lane.lane_id].join('、')} aria-label={`注意: ${issues[lane.lane_id].join('、')}`}>▲</span>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="help">画像上の枠はドラッグか矢印キー（Shiftで10px）で動かします。画像の外には出ません。</p>
    </section>

    <section aria-label="結果">
      <h2>結果</h2>
      <div class="actions">
        <button class="primary" disabled={!canCompute} title={confirmed ? '' : '元データの条件を確認してください'} on:click={oncompute}>計算</button>
        {#if current}
          <a class="button" href={excelUrl} download="quantification.xlsx" data-testid="quant-excel">Excel</a>
          <a class="button" href={csvUrl} download={csvName} data-testid="quant-csv">CSV</a>
        {:else}<button disabled title="計算してから出力できます">Excel</button><button disabled title="計算してから出力できます">CSV</button>{/if}
        {#if pending}<span class="help" role="status">位置を保存中</span>{/if}
        {#if !confirmed}<span class="help">計算の前に、元データの条件を確認してください。</span>{/if}
      </div>
      {#if !results}
        <p class="empty-note" data-testid="quant-results-empty">未計算です。</p>
      {:else if !current}
        <p class="warn" role="alert" data-testid="quant-stale">▲ 設定や元画像が変わったため、再計算が必要です。{info?.reason ? `（${info.reason}）` : ''}</p>
      {:else}
        <p class="help">枠 {group.width_px} × {group.height_px} px（面積は全レーン同じ）・信号の向き: {group.signal_polarity === 'low' ? '低値が強い' : '高値が強い'}・飽和しきい値: {group.saturation_value ?? '未指定'}。正規化はしていません。</p>
        <div class="table-wrap">
          <table class="results" data-testid="quant-results">
            <thead><tr><th scope="col">レーン</th><th scope="col">サンプル</th><th scope="col" class="r">面積 (px)</th><th scope="col" class="r">積分強度</th>
              <th scope="col" class="r">背景平均</th><th scope="col" class="r">補正後</th><th scope="col" class="r">飽和画素</th><th scope="col">状態</th></tr></thead>
            <tbody>
              {#each results.rows as row, i (row.lane_id)}
                <tr data-lane={row.lane_id} class:selected={row.lane_id === selectedLaneId}>
                  <th scope="row"><button class="lane" aria-pressed={row.lane_id === selectedLaneId} on:click={() => selectedLaneId = row.lane_id}>{row.lane_id}</button></th>
                  <td class="sample" title={row.sample_name || ''}>{row.sample_name || laneName(group, project, row.lane_id, i) || '—'}</td>
                  <td class="r">{formatNumber(row.area_px)}</td>
                  <td class="r">{formatNumber(row.raw_sum, 1)}</td>
                  <td class="r">{formatNumber(row.background_mean, 2)}</td>
                  <td class="r net">{formatNumber(row.net_signal, 1)}</td>
                  <td class="r">{formatNumber(row.saturated_pixels)}</td>
                  <td><span class="mark" aria-hidden="true">{statusMark(row)}</span> {statusLabel(row)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .quant { padding: 12px 16px 24px; overflow: auto; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
  section { border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 8px 12px 12px; }
  h2 { margin: 0 0 6px; font-size: var(--text-body); font-weight: 600; color: #33474f; display: flex; align-items: center; gap: 8px; }
  .note, .help, .unit, .empty-note { font-size: var(--text-meta); color: var(--muted); margin: 0; text-wrap: pretty; }
  .help { margin-top: 4px; }
  .warn { margin: 0; font-size: var(--text-meta); color: #9a3412; background: #fff6ed; border: 1px solid #f3c9a5; border-radius: 6px; padding: 6px 10px; }
  .error { margin: 4px 0 0; font-size: var(--text-meta); color: #a33b32; font-weight: 600; }
  .rows { display: flex; flex-direction: column; gap: 4px; max-height: 188px; overflow: auto; }
  .row { display: flex; align-items: center; gap: 8px; text-align: left; padding: 5px 10px; border-radius: 6px; }
  .row.active, .chip.active { border-color: var(--teal); background: #f1f6f6; }
  .row .name { font-weight: 600; flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .meta { color: var(--muted); font-size: var(--text-meta); }
  .count { margin-left: auto; font-size: var(--text-meta); color: var(--teal); white-space: nowrap; }
  .start, .actions, .size { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  .groups { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .chip { display: inline-flex; align-items: center; gap: 8px; padding: 3px 10px; border-radius: 99px; font-size: var(--text-meta); }
  .chip .state { color: var(--muted); }
  select { font: inherit; border: 1px solid #c5d0d7; border-radius: 5px; padding: 4px 8px; background: #fff; color: #263c49; }
  label { font-size: var(--text-meta); color: var(--secondary); }
  .field { display: inline-flex; align-items: center; gap: 6px; }
  .field .num { width: 96px; }
  .num { width: 64px; padding: 2px 6px; text-align: right; font-size: var(--text-meta); }
  input[aria-invalid='true'] { border-color: #a33b32; }
  .polarity { border: 0; margin: 0 0 8px; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
  .polarity legend { font-size: var(--text-meta); color: var(--secondary); padding: 0; margin-bottom: 2px; }
  .radio, .check { display: inline-flex; align-items: center; gap: 6px; color: #263c49; font-size: var(--text-body); }
  .radio input, .check input, td.flag input { width: auto; accent-color: var(--teal); margin: 0; }
  .check { margin-top: 8px; }
  .badge { font-size: var(--text-meta); font-weight: 500; color: #9a3412; }
  .badge.ok { color: #0b6b5f; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: var(--text-meta); }
  th, td { padding: 3px 6px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; font-weight: 400; }
  thead th { color: var(--muted); font-weight: 500; }
  .r { text-align: right; }
  td.sample { max-width: 14ch; overflow: hidden; text-overflow: ellipsis; }
  td.xy { white-space: nowrap; }
  td.xy .num { width: 56px; margin-right: 2px; }
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
