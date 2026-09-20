// App-wide constants and the per-phone settings kept in localStorage.

export const POLL_MS = 1000;           // one small JSON per second; see docs/APP.md for why not SSE
export const REQUEST_TIMEOUT_MS = 4000;
export const LOST_AFTER_MS = 6000;     // no answer from the platform for this long -> "連線中斷"

const KEY = 'riderApp.';

function read(name, fallback) {
  try {
    const raw = localStorage.getItem(KEY + name);
    return raw == null ? fallback : JSON.parse(raw);
  } catch (e) { return fallback; }  // private mode / corrupted value
}

function write(name, value) {
  try { localStorage.setItem(KEY + name, JSON.stringify(value)); } catch (e) { /* private mode */ }
}

export const DEFAULT_CHANNELS = { sound: true, voice: true, vibration: true, notification: true };

export const settings = {
  // Empty = same origin (the normal PWA case). A store-packaged build (Capacitor)
  // has no origin of its own, so it needs the platform's address here.
  get apiBase() { return read('apiBase', ''); },
  set apiBase(value) { write('apiBase', String(value || '').replace(/\/+$/, '')); },

  get riderId() { return read('riderId', null); },
  set riderId(value) { write('riderId', value); },

  get channels() { return { ...DEFAULT_CHANNELS, ...read('channels', {}) }; },
  set channels(value) { write('channels', value); },

  // Demo ride: simulated GPS that travels along the route (geo/rideSimulator.js). ?demo_ride=1 forces it on.
  get demoRide() { return new URLSearchParams(location.search).has('demo_ride') || read('demoRide', false); },
  set demoRide(value) { write('demoRide', Boolean(value)); },
};
