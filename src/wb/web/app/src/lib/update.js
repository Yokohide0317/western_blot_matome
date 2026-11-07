// The update notice: the hub (`../../api/hub`) says whether a newer release exists.
export const DISMISSED_KEY = 'wbm.update.dismissed';

export function storage() {
  try { return globalThis.localStorage ?? null; } catch { return null; }
}

export function dismissedVersion(store) {
  try { return store?.getItem(DISMISSED_KEY) ?? null; } catch { return null; }
}

export function dismiss(store, version) {
  try { store?.setItem(DISMISSED_KEY, version); } catch { /* private mode: ask again next time */ }
}

export const shouldShow = (info, dismissed) => Boolean(info?.available && info.latest && info.latest !== dismissed);

// Poll until the restarted hub reports `version`; false after `timeout` ms.
export async function waitForVersion(readVersion, version, {
  interval = 2000, timeout = 180000, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), now = Date.now,
} = {}) {
  const deadline = now() + timeout;
  while (now() < deadline) {
    await sleep(interval);
    try {
      if (await readVersion() === version) return true;
    } catch { /* the hub is restarting */ }
  }
  return false;
}
