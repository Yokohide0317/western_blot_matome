export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export async function request(path, payload) {
  const response = await fetch(`api/${path}`, payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail ?? data.message;
    const message = typeof detail === 'string' ? detail : detail?.message || '処理できませんでした。入力を確認してください。';
    throw new ApiError(response.status, message);
  }
  return data;
}
export const frameUrl = id => `api/image/frame/${encodeURIComponent(id)}.png?max_width=1400`;
export const panelUrl = (panel, capture, revision) => `api/image/panel/${encodeURIComponent(panel)}/${encodeURIComponent(capture)}.png?v=${revision}`;
export const markerUrl = (panel, revision) => `api/image/marker/${encodeURIComponent(panel)}.png?v=${revision}`;

export const isIncluded = panel => panel.included !== false;

// Quantification views use the raw-derived preview and the server's freshness check, never the figure images.
export const quantificationInfoPath = id => `quantification_info/${encodeURIComponent(id)}`;
export const quantificationSourceUrl = group => `api/quantification_source/${encodeURIComponent(group.group_id)}.png?v=${encodeURIComponent(`${group.source_asset_id}:${group.frame_id}`)}`;
export const quantificationCsvUrl = id => `api/quantification_csv/${encodeURIComponent(id)}`;

export const quantificationExcelUrl = id => `api/quantification_excel/${encodeURIComponent(id)}`;
export const quantificationExcelAllUrl = 'api/quantification_excel';
