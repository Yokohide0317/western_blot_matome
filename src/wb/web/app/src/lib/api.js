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
