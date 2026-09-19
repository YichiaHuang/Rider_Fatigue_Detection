// Client-side state. api/client.js writes here, components read from here and
// re-render on change. Components never talk to each other or to the network.

const MAX_EVENTS = 200;

export function createStore() {
  const state = {
    config: null,
    riders: new Map(),   // id -> rider object from the API, plus .history [[t, score], ...]
    order: [],           // rider ids in display order
    selectedId: null,    // rider shown large; null = the one the server marks primary
    events: [],          // newest first
    connection: 'connecting',  // connecting | live | lost
    board: { reachable: false },
  };
  const listeners = new Set();
  let scheduled = false;

  function notify() {
    // Coalesce bursts (5 riders update in the same millisecond) into one render.
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      listeners.forEach((fn) => fn(state));
    });
  }

  function trim(history) {
    const keep = state.config ? state.config.history_seconds : 600;
    const cutoff = Date.now() / 1000 - keep;
    while (history.length && history[0][0] < cutoff) history.shift();
  }

  return {
    state,
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },

    setConfig(config) { state.config = config; notify(); },
    setConnection(value) { if (state.connection !== value) { state.connection = value; notify(); } },
    setBoard(board) { state.board = board; notify(); },

    select(id) {
      if (state.selectedId === id) return;
      state.selectedId = id;
      state.board = { reachable: false, pending: true };  // never show rider A's camera under rider B's name
      notify();
    },

    selectedRider() {
      const riders = [...state.riders.values()];
      return state.riders.get(state.selectedId) || riders.find((r) => r.primary) || riders[0] || null;
    },

    loadSnapshot(snapshot) {
      state.order = snapshot.riders.map((r) => r.id);
      snapshot.riders.forEach((r) => state.riders.set(r.id, { ...r, history: r.history || [] }));
      state.events = [...snapshot.events].reverse().slice(0, MAX_EVENTS);
      notify();
    },

    applyRider(update) {
      const current = state.riders.get(update.id);
      const history = current ? current.history : [];
      const last = history[history.length - 1];
      if (update.received_at != null && update.score != null && (!last || update.received_at > last[0])) {
        history.push([update.received_at, update.score]);
        trim(history);
      }
      state.riders.set(update.id, { ...update, history });
      if (!state.order.includes(update.id)) state.order.push(update.id);
      notify();
    },

    addEvent(event) {
      if (state.events.some((e) => e.seq === event.seq)) return;
      state.events.unshift(event);
      state.events.length = Math.min(state.events.length, MAX_EVENTS);
      notify();
    },
  };
}
