<script>
  import { onMount } from 'svelte';
  import { ApiError, request, isIncluded, quantificationSourceUrl, quantificationCsvUrl, quantificationExcelAllUrl, quantificationInfoPath } from './lib/api.js';
  import { sameLanes, moveRoi, setRoi, updateLane } from './lib/quantification.js';
  import TopBar from './components/TopBar.svelte';
  import FigurePane from './components/FigurePane.svelte';
  import SourceCanvas from './components/SourceCanvas.svelte';
  import ExposureStrip from './components/ExposureStrip.svelte';
  import QuantificationPane from './components/QuantificationPane.svelte';
  import QuantificationCanvas from './components/QuantificationCanvas.svelte';

  let project = null, selectedPanelId = '', selectedRegionId = '', preview = null, mode = 'lum', manualEmpty = false;
  let busy = false, dragging = false, polling = false, toast = null, toastTimer, queue = Promise.resolve();
  let stage = 'membrane', drawRequest = null;
  let editHistory = {can_undo: false, can_redo: false};
  function addMembrane() { manualEmpty = true; drawRequest = {kind: 'membrane', token: Date.now()}; }
  const editing = new Set();
  $: panels = [...(project?.panels || [])].sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0));
  $: if (panels.length) manualEmpty = false;
  $: membranes = (project?.membrane_regions || []).map(region => ({region})).sort((a,b) => a.region.user_label.localeCompare(b.region.user_label, undefined, {numeric:true}));
  // Band selection only shows membranes in the figure, so an excluded choice falls back to the first included one.
  $: chosenRegion = project?.membrane_regions.find(r => r.region_id === selectedRegionId);
  $: activeRegion = stage === 'band'
    ? (chosenRegion?.included !== false && chosenRegion) || membranes.find(m => m.region.included !== false)?.region || membranes[0]?.region
    : chosenRegion || membranes[0]?.region;
  $: regionPanels = panels.filter(p => p.region_id === activeRegion?.region_id);
  $: panel = regionPanels.find(p => p.panel_id === selectedPanelId) || regionPanels.find(isIncluded) || regionPanels[0];
  $: canvasPanel = panel || (activeRegion ? {panel_id:null, region_id:activeRegion.region_id, image_set_id:activeRegion.image_set_id} : null);
  $: if (preview && panel && !project.captures.some(c => c.capture_id === preview)) preview = null;

  function notify(text, kind = 'info', path = '') {
    toast = {text, kind, path};
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast = null, path ? 9000 : 4500);
  }
  const fail = e => notify(e.message || '読み込みに失敗しました', 'error');
  async function load() {
    const next = await request('project');
    project = next;
    const history = await request('edit_history');
    editHistory = history.project_revision === next.project_revision ? history : {can_undo: false, can_redo: false};
    if (!selectedRegionId) {
      const regions = [...next.membrane_regions].sort((a,b) => a.user_label.localeCompare(b.user_label, undefined, {numeric:true}));
      selectedRegionId = regions.find(r => r.included)?.region_id || regions[0]?.region_id || '';
    }
  }
  // Changes run one at a time so each uses the revision left by the previous one.
  function act(task, message) {
    const run = queue.then(async () => {
      busy = true;
      try {
        const result = await task();
        await load();
        if (message) notify(message);
        return result ?? true;
      } catch (e) {
        if (e.status === 409) { await load().catch(fail); notify('外部で変更されています。最新を読み込みました', 'error'); }
        else fail(e);
        return false;
      } finally { busy = false; }
    });
    queue = run.catch(() => {});
    return run;
  }
  const mutate = (operation, payload, message) => act(() => request(operation, {...payload, expected_revision: project.project_revision}), message);

  $: historyBusy = busy || dragging || qDraft !== null;
  async function moveHistory(direction) {
    if (historyBusy || !editHistory[`can_${direction}`]) return;
    if (await mutate(direction, {}, direction === 'undo' ? '元に戻しました' : 'やり直しました')) preview = null;
  }
  function historyKey(event) {
    if (event.defaultPrevented || event.altKey || !(event.ctrlKey || event.metaKey)
      || event.target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
    const key = event.key.toLowerCase();
    const direction = key === 'z' ? (event.shiftKey ? 'redo' : 'undo') : key === 'y' && event.ctrlKey ? 'redo' : null;
    if (!direction) return;
    event.preventDefault();
    if (!event.repeat) moveHistory(direction);
  }

  function selectPanel(id) {
    const target = panels.find(p => p.panel_id === id);
    if (busy || dragging || !target) return;
    selectedRegionId = target.region_id;
    selectedPanelId = id; preview = null;
  }
  function selectRegion(id) {
    if (busy || dragging) return;
    selectedRegionId = id; selectedPanelId = ''; preview = null;
  }
  const rename = (id, label) => mutate('rename_panel', {panel_id: id, label}, 'ラベルを変更しました');
  const deleteRow = id => mutate('delete_panel', {panel_id:id}, 'Selectionを削除しました');
  const addRow = id => mutate('duplicate_panel', {panel_id: id}, 'Selectionを複製しました');
  async function addManualMembrane(rect, referenceFrameId) {
    const before = new Set(project.membrane_regions.map(r => r.region_id));
    const result = await mutate('add_manual_membrane', {rect, create_selection:false,
      ...(referenceFrameId ? {reference_frame_id: referenceFrameId} : {}),
    }, 'メンブレンを追加しました');
    if (result && result !== true) {
      const created = result.membrane_regions.find(r => !before.has(r.region_id));
      if (created) { selectedRegionId = created.region_id; selectedPanelId = ''; preview = null; manualEmpty = false; }
    }
    return result;
  }
  async function addPanelSelection(panelId, rect) {
    const before = new Set((project?.panels || []).map(p => p.panel_id));
    const result = await mutate('add_panel_selection', {panel_id: panelId, region_id: activeRegion.region_id, crop_rect: rect}, 'セレクションを追加しました');
    if (result && result !== true) {
      const created = (result.panels || []).find(p => !before.has(p.panel_id));
      if (created) { selectedPanelId = created.panel_id; preview = null; }
    }
    return result;
  }
  const setIncluded = (id, included) => mutate('set_panel_included', {panel_id: id, included}, included ? '段を表示しました' : '段を非表示にしました');
  function setMembraneIncluded(regionId, included) {
    // Switching a membrane off must not make it the one being edited.
    if (included) selectRegion(regionId);
    return mutate('set_membrane_included', {region_id: regionId, included});
  }
  const reorder = ids => mutate('reorder_panels', {panel_ids: ids});
  // Master Control: one lane count for every row in the figure.
  $: figurePanels = panels.filter(p => isIncluded(p) && project?.membrane_regions.some(r => r.region_id === p.region_id && r.included !== false));
  $: figureCounts = [...new Set(figurePanels.map(p => project.sample_groups.find(g => g.sample_group_id === p.sample_group_id)?.lane_count
    ?? p.display_geometry?.lane_layout?.lane_count ?? project.membrane_regions.find(r => r.region_id === p.region_id)?.lane_count ?? null))];
  $: masterCount = figureCounts.length === 1 ? figureCounts[0] : null;
  $: masterBase = Math.max(0, ...figureCounts.filter(Boolean));
  const setAllLanes = count => setLanes(Math.max(1, count), null);
  const setLanes = (count, panelId, names) => mutate('set_lanes', {lane_count: count, panel_id: panelId, ...(names ? {sample_names: names} : {})});
  const setNames = (groupId, values) => mutate('set_sample_names', {sample_group_id: groupId, samples: values.map(name => ({name}))});
  const saveLaneBoundaries = (id, boundaries) => mutate('set_lane_boundaries', {panel_id: id, boundaries}, 'レーン境界を保存しました');
  const setMarker = (id, range) => mutate('set_marker_x', {panel_id: id, x0: range?.x0 ?? null, x1: range?.x1 ?? null},
    range ? 'マーカーの範囲を保存しました' : 'マーカーを自動に戻しました');
  const saveGeometry = (id, geometry) => mutate('save_panel_edit', {panel_id: id, display_geometry: geometry}, '編集を保存しました');
  const rotateSelection = (id, degrees) => mutate('rotate_panel', {panel_id: id, degrees}, '向きを変更しました');
  async function adopt(captureId) {
    if (await mutate('set_selected_exposure', {panel_id: panel.panel_id, capture_id: captureId}, '採用露光を変更しました')) preview = null;
  }
  async function write(kind) {
    const result = await act(() => request('export', {kind}));
    if (!result?.path) return;
    notify('保存しました', 'info', result.path);
    // Hand each written file to the browser as a download; the copy in wblot_output stays.
    for (const name of result.files || []) {
      const link = document.createElement('a');
      link.href = `api/download/${encodeURIComponent(name)}`;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
    }
  }
  const setEditing = (key, on) => { if (on) editing.add(key); else editing.delete(key); };

  // Quantification (optional third step). ROI positions are full-resolution raw pixels; the server validates every change.
  let qPanelId = '', qGroupId = '', qLaneId = null, qDraft = null, qTimer, qSaving = false;
  let qInfo = null, qInfoError = '', qDims = {}, qInfoToken = 0;
  $: qGroups = project?.quantification_groups || [];
  $: qPanels = panels.filter(p => figurePanels.includes(p) || qGroups.some(g => g.panel_id === p.panel_id));
  $: if (qPanels.length && !qPanels.some(p => p.panel_id === qPanelId)) qPanelId = qPanels[0].panel_id;
  $: qPanelGroups = qGroups.filter(g => g.panel_id === qPanelId);
  $: if (!qPanelGroups.some(g => g.group_id === qGroupId)) qGroupId = qPanelGroups[0]?.group_id || '';
  $: qGroup = qGroups.find(g => g.group_id === qGroupId) || null;
  $: qLanes = qDraft ?? qGroup?.lanes ?? [];
  $: if (qGroup && !qLanes.some(l => l.lane_id === qLaneId)) qLaneId = qLanes[0]?.lane_id ?? null;
  $: qImage = qInfo?.group_id === qGroupId ? {width: qInfo.width, height: qInfo.height} : qDims[qGroupId] || null;
  $: qSize = {width: qGroup?.width_px ?? 1, height: qGroup?.height_px ?? 1};
  $: setEditing('quant', qDraft !== null);
  $: loadQuantInfo(stage === 'quantification' ? qGroup : null, project?.project_revision);
  async function loadQuantInfo(group, revision) {
    const token = ++qInfoToken;
    if (!group) { qInfo = null; qInfoError = ''; return; }
    const id = group.group_id;
    // Dimensions never change for a group; only a saved result needs its freshness checked again.
    if (qDims[id] && !group.results) { qInfo = {...qDims[id], group_id: id, results_current: false}; qInfoError = ''; return; }
    try {
      const info = await request(quantificationInfoPath(id));
      if (token !== qInfoToken) return;
      if (!(info.width > 0 && info.height > 0)) { qInfo = null; qInfoError = info.reason || '元画像を確認できません'; return; }
      qDims = {...qDims, [id]: {width: info.width, height: info.height}};
      qInfo = {...info, group_id: id}; qInfoError = '';
    } catch (e) {
      if (token !== qInfoToken) return;
      qInfo = null; qInfoError = e.message || '元画像を確認できません';
    }
  }
  async function qStart(panelId, captureId) {
    const before = new Set(qGroups.map(g => g.group_id));
    const result = await mutate('start_quantification', {panel_id: panelId, ...(captureId ? {capture_id: captureId} : {})}, '定量を開始しました');
    const created = result && result !== true && (result.quantification_groups || []).find(g => !before.has(g.group_id));
    if (created) { qPanelId = created.panel_id; qGroupId = created.group_id; qLaneId = null; }
  }
  const qSettings = (patch, message) => mutate('update_quantification', {group_id: qGroup.group_id, ...patch}, message);
  const qCompute = () => mutate('compute_quantification', {group_id: qGroup.group_id}, '計算しました');
  async function downloadQuantificationExcel() {
    if (busy || dragging || qDraft !== null) return;
    busy = true;
    try {
      const response = await fetch(quantificationExcelAllUrl);
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(response.status, typeof data.detail === 'string' ? data.detail : 'Excelを出力できませんでした');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = 'quantification.xlsx';
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { fail(e); }
    finally { busy = false; }
  }
  // Edits show at once; one save follows when the pointer is released or the keys pause.
  function qEdit(change, when) {
    if (!qGroup || !qImage) return;
    qDraft = change(qDraft ?? qGroup.lanes);
    clearTimeout(qTimer);
    if (when === 'now') qCommit(); else if (when === 'soon') qTimer = setTimeout(qCommit, 400);
  }
  const qMoveTo = (laneId, kind, x, y, when) => qEdit(lanes => updateLane(lanes, laneId, l => setRoi(l, kind, x, y, qSize, qImage)), when);
  const qNudge = (laneId, kind, dx, dy) => qEdit(lanes => updateLane(lanes, laneId, l => moveRoi(l, kind, dx, dy, qSize, qImage)), 'soon');
  async function qCommit() {
    clearTimeout(qTimer);
    if (qSaving) return; // the loop below picks up edits made while a save runs
    qSaving = true;
    try {
      while (qDraft && qGroup && !dragging) {
        const lanes = qDraft;
        if (sameLanes(lanes, qGroup.lanes)) { qDraft = null; break; }
        const ok = await mutate('update_quantification', {group_id: qGroup.group_id, lanes});
        if (!ok) { qDraft = null; break; }
        if (qDraft === lanes) qDraft = null;
      }
    } finally { qSaving = false; }
  }
  function qDrag(on) { dragging = on; }

  async function poll() {
    if (polling || busy || dragging || editing.size || !project) return;
    polling = true;
    try {
      const revision = await request('revision');
      if (!busy && !dragging && !editing.size && revision.project_revision !== project.project_revision) {
        await load();
        notify('外部の変更を読み込みました');
      }
    } catch (e) { fail(e); }
    finally { polling = false; }
  }
  onMount(() => {
    load().catch(fail);
    const timer = setInterval(poll, 2000);
    return () => { clearInterval(timer); clearTimeout(toastTimer); clearTimeout(qTimer); };
  });
</script>

<svelte:window on:keydown={historyKey}/>
<svelte:head><title>Western Blot Matome (WBM)</title></svelte:head>
{#if project}
  <div class="workbench" aria-busy={busy}>
    <TopBar {project} {busy} {historyBusy} canUndo={editHistory.can_undo} canRedo={editHistory.can_redo}
      onundo={() => moveHistory('undo')} onredo={() => moveHistory('redo')} onexport={write}/>
    {#if membranes.length || manualEmpty || project.captures.some(c => c.modality === 'colorimetric')}
      <div class="workspace" class:quant-workspace={stage === 'quantification'}>
        <section aria-label="図">
          <div class="stage-tabs" role="tablist" aria-label="手順">
            <button role="tab" disabled={busy || dragging || qDraft !== null} aria-selected={stage === 'membrane'} on:click={() => stage = 'membrane'}>1. メンブレン選択</button>
            <button role="tab" disabled={busy || dragging || qDraft !== null} aria-selected={stage === 'band'} on:click={() => stage = 'band'}>2. バンド選択</button>
            <button role="tab" disabled={busy || dragging || qDraft !== null} aria-selected={stage === 'quantification'} on:click={() => stage = 'quantification'}>3. 定量(Optional)</button>
          </div>
          {#if stage === 'membrane'}
            <div class="membrane-list" data-testid="membrane-list">
              {#each membranes as item (item.region.region_id)}
                <div class="membrane-entry">
                  <div class="membrane-heading">
                    <button class="membrane-item" class:active={item.region.region_id === activeRegion?.region_id} class:excluded={item.region.included === false} disabled={busy}
                      data-region={item.region.region_id} on:click={() => selectRegion(item.region.region_id)}>
                      <span class="m-label">{item.region.user_label}</span>
                    </button>
                    <button class="membrane-switch" role="switch" aria-checked={item.region.included !== false}
                      aria-label={`${item.region.user_label} を図に含める`} disabled={busy}
                      on:click={() => setMembraneIncluded(item.region.region_id, item.region.included === false)}>
                      <span class="switch-knob"></span>
                    </button>
                  </div>
                </div>
              {/each}
              <button class="add-membrane" aria-label="メンブレンを追加" title="メンブレンを追加" disabled={busy} on:click={addMembrane}>＋</button>
              <button class="primary next" disabled={busy || !activeRegion} on:click={() => stage = 'band'}>次へ</button>
            </div>
          {:else if stage === 'band'}
            <div class="master-control" role="group" aria-label="Master Control">
              <span class="master-title">Master Control</span>
              <span class="master-item">全段のレーン数
                {#if masterBase}
                  <button class="icon" aria-label="全段のレーンを減らす" title="全段のレーンを減らす" disabled={busy || !figurePanels.length || masterBase <= 1}
                    on:click={() => setAllLanes((masterCount ?? masterBase) - 1)}>−</button>
                  <b data-testid="master-lanes">{masterCount ?? '混在'}</b>
                  <button class="icon" aria-label="全段のレーンを増やす" title="全段のレーンを増やす" disabled={busy || !figurePanels.length}
                    on:click={() => setAllLanes((masterCount ?? masterBase) + 1)}>+</button>
                {:else}
                  <button class="add-lane" aria-label="全段のレーンを設定" disabled={busy || !figurePanels.length} on:click={() => setAllLanes(1)}>レーン +</button>
                {/if}
              </span>
            </div>
            <FigurePane {project} {panels} selectedPanelId={panel?.panel_id} activeRegionId={activeRegion?.region_id} {preview} {busy} onselect={selectPanel} onrename={rename}
              onadd={addRow} onsetincluded={setIncluded} ondelete={deleteRow} onreorder={reorder} onlanes={setLanes} onsetnames={setNames} onlaneedges={saveLaneBoundaries} onediting={setEditing}/>
          {:else}
            <QuantificationPane {project} panels={qPanels} {busy} pending={qDraft !== null || dragging} bind:panelId={qPanelId} bind:groupId={qGroupId} bind:selectedLaneId={qLaneId}
              group={qGroup} lanes={qLanes} info={qInfo} infoError={qInfoError} csvUrl={qGroup ? quantificationCsvUrl(qGroup.group_id) : ''} onexcel={downloadQuantificationExcel}
              onstart={qStart} onsettings={qSettings} onlane={(laneId, change, immediate) => qEdit(lanes => updateLane(lanes, laneId, change), immediate ? 'now' : 'soon')}
              oncompute={qCompute} onplace={() => mutate('place_quantification', {group_id: qGroup.group_id}, 'バンド選択から枠を配置しました')}/>
          {/if}
        </section>
        <section aria-label="元画像">
        {#if stage === 'quantification'}
          <QuantificationCanvas group={qGroup} image={qImage} imageUrl={qGroup ? quantificationSourceUrl(qGroup) : ''} lanes={qLanes} selectedLaneId={qLaneId} loadError={qInfoError}
            busy={busy || qDraft !== null} onresize={patch => qSettings(patch, '全レーンの枠サイズを変更しました')} onselect={id => qLaneId = id} onedit={(laneId, kind, x, y) => qMoveTo(laneId, kind, x, y, null)} onnudge={qNudge} oncommit={qCommit} ondrag={qDrag}/>
        {:else}<SourceCanvas {drawRequest} {project} panel={canvasPanel} {preview} bind:mode {busy} {stage} initialDraw={manualEmpty && !panels.length ? 'membrane' : null}
          onselect={selectPanel} onselectregion={selectRegion} onsave={saveGeometry} onmarker={setMarker} onrotate={rotateSelection}
          onaddmembrane={addManualMembrane} onaddselection={addPanelSelection} ondrag={value => dragging = value}/>
        {/if}</section>
      </div>
    {:else}
      <div class="workspace"><div class="empty">
        <button disabled={busy} on:click={addMembrane}>＋</button>
      </div></div>
    {/if}
    {#if stage === 'band' && panel}<ExposureStrip {project} {panel} {preview} {busy} onpreview={id => preview = id} onadopt={adopt}/>{/if}
  </div>
{:else}<div class="workbench"><div class="workspace"><div class="empty">読み込み中</div></div></div>{/if}
{#if toast}<div class="toast" class:error={toast.kind === 'error'} role={toast.kind === 'error' ? 'alert' : 'status'}>{toast.text}{#if toast.path}<code>{toast.path}</code>{/if}</div>{/if}
