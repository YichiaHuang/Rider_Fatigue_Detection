// Data layer: the ONLY file that knows backend URLs. Screens never fetch.
// Contract: docs/API.md section 4.

import { REQUEST_TIMEOUT_MS, settings } from '../config.js';

async function request(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(settings.apiBase + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(data.error || `${path} -> ${res.status}`);
      error.status = res.status;
      error.state = data.state;  // 409 carries the current state so the screen can catch up
      throw error;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

const rider = (id) => `/api/app/${encodeURIComponent(id)}`;

export const api = {
  config: () => request('/api/config'),
  riders: () => request('/api/app/riders'),
  state: (id) => request(`${rider(id)}/state`),
  // Every action answers with the new state, so the screen updates without waiting for the next poll.
  setDuty: (id, on) => request(`${rider(id)}/duty`, { on }),
  answerOffer: (id, orderId, accept) => request(`${rider(id)}/offer`, { order_id: orderId, accept }),
  advanceOrder: (id, orderId) => request(`${rider(id)}/advance`, { order_id: orderId }),
  acknowledge: (id, level) => request(`${rider(id)}/ack`, { level }),
  // Road route for the order on screen; position ({lat, lng} | null) adds the leg from the rider.
  route: (id, position) => request(`${rider(id)}/route${
    position ? `?from=${position.lat.toFixed(5)},${position.lng.toFixed(5)}` : ''}`),
};
