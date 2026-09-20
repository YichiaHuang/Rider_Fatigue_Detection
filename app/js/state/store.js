// Client-side state. main.js writes here (from api/client.js), screens and the
// alert engine read from here. Screens never talk to each other or to the network.

import { LEVEL_UNKNOWN, deriveLevel } from './alertLevel.js';

export function createStore() {
  const state = {
    config: null,        // GET /api/config: warn / pause / resume thresholds
    riderId: null,
    app: null,           // GET /api/app/{id}/state: rider + duty + offer + order + stats
    level: LEVEL_UNKNOWN,  // normal | warning | paused | unknown (state/alertLevel.js)
    connection: 'connecting',  // connecting | live | lost — the link phone <-> platform
    alert: null,         // what the full-screen overlay shows: { level, hasOrder } | null
    position: null,      // phone GPS { lat, lng, accuracy } | null (geo/position.js)
    route: null,         // GET /api/app/{id}/route for the order on screen | null (state/routeSync.js)
    navigating: false,   // full-screen in-app navigation is open (screens/navigation.js)
  };
  const listeners = new Set();
  let scheduled = false;

  function notify() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      listeners.forEach((fn) => fn(state));
    });
  }

  return {
    state,
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },

    setConfig(config) { state.config = config; notify(); },
    setConnection(value) { if (state.connection !== value) { state.connection = value; notify(); } },
    setAlert(alert) { state.alert = alert; notify(); },
    setPosition(position) { state.position = position; notify(); },
    setRoute(route) { state.route = route; notify(); },
    setNavigating(on) { if (state.navigating !== on) { state.navigating = on; notify(); } },

    setRider(riderId) {
      state.riderId = riderId;
      state.app = null;
      state.level = LEVEL_UNKNOWN;
      state.alert = null;
      state.route = null;
      state.navigating = false;
      notify();
    },

    applyAppState(appState) {
      if (appState.rider.id !== state.riderId) return;  // answer for a rider we've signed out of
      state.app = appState;
      state.level = deriveLevel(appState, state.config, state.level);
      notify();
    },
  };
}
