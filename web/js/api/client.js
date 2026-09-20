// Data layer: the ONLY file that knows backend URLs. Components never fetch.
// Contract: docs/API.md

const BASE = '';  // same origin; set to 'http://host:8000' to point at another backend

async function getJson(path) {
  const res = await fetch(BASE + path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function postJson(path, payload, what) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${what} (${res.status})`);
  return body;
}

export const api = {
  config: () => getJson('/api/config'),
  sources: () => getJson('/api/sources'),
  state: (historySeconds) => getJson(`/api/state?history=${historySeconds}`),
  // Every board call is per rider: each board runs its own stream server.
  boardStatus: (riderId) => getJson(`/api/board/${encodeURIComponent(riderId)}/status`),
  boardStreamUrl: (riderId) => `${BASE}/api/board/${encodeURIComponent(riderId)}/stream.mjpg?t=${Date.now()}`,

  setBoardMode: (riderId, demo) => postJson(`/api/board/${encodeURIComponent(riderId)}/mode`, { demo }, 'mode switch failed'),
  // Demo helper: the board zeroes its score and the platform lifts the pause.
  resetBoardScore: (riderId) => postJson(`/api/board/${encodeURIComponent(riderId)}/reset`, {}, 'reset failed'),

  // Live updates. EventSource reconnects by itself; onOpen fires on every
  // (re)connect so the caller can re-fetch a snapshot and fill any gap.
  subscribe({ onRider, onEvent, onOpen, onError }) {
    const source = new EventSource(BASE + '/api/events');
    source.addEventListener('open', () => onOpen && onOpen());
    source.addEventListener('error', () => onError && onError());
    source.addEventListener('rider', (e) => onRider(JSON.parse(e.data)));
    source.addEventListener('event', (e) => onEvent(JSON.parse(e.data)));
    return () => source.close();
  },
};
