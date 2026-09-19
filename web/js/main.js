// Wiring only: api -> store -> components. No rendering or fetch logic here.
import { api } from './api/client.js';
import { createEventLog } from './components/eventLog.js';
import { createHeader } from './components/header.js';
import { createPrimaryRider } from './components/primaryRider.js';
import { createRiderCard } from './components/riderCard.js';
import { createVideoPanel } from './components/videoPanel.js';
import { createStore } from './state/store.js';

const BOARD_POLL_MS = 2000;
const AGE_TICK_MS = 1000;
// ?once = load one snapshot and stop (no SSE, no timers). For screenshots and layout debugging.
const ONCE = new URLSearchParams(location.search).has('once');
if (ONCE) {
  // A pending image holds the window load event, which is what headless
  // `--screenshot` waits for — gives the snapshot time to render first.
  const hold = new Image();
  hold.src = '/api/debug/delay?ms=5000';
  hold.hidden = true;
  document.body.append(hold);
}

async function start() {
  const store = createStore();
  const config = await api.config();
  store.setConfig(config);

  const header = createHeader(document.getElementById('header'));
  const video = createVideoPanel(document.getElementById('video'), {
    streamUrl: api.boardStreamUrl,
    onToggleFocus: () => setFocus(!focused),
    onToggleMode: async (demo) => {
      const result = await api.setBoardMode(store.selectedRider().id, demo);
      store.setBoard({ ...result, configured: true });
    },
  });
  const primary = createPrimaryRider(
    document.getElementById('primary-readout'), document.getElementById('primary-chart'), config);
  const eventLog = createEventLog(document.getElementById('events'));

  // Video focus: big camera picture, the rest of the dashboard shrinks around it.
  // Remembered per browser; ?focus=1 pins it from the URL (projector bookmark).
  const app = document.querySelector('.app');
  let focused = false;
  function setFocus(on) {
    focused = on;
    app.classList.toggle('video-focus', on);
    video.setFocus(on);
    primary.setCompact(on);
    try { localStorage.setItem('videoFocus', on ? '1' : '0'); } catch (e) { /* private mode */ }
  }
  const focusParam = new URLSearchParams(location.search).get('focus');
  let savedFocus = null;
  try { savedFocus = localStorage.getItem('videoFocus'); } catch (e) { /* private mode */ }
  setFocus(focusParam != null ? focusParam !== '0' : savedFocus === '1');
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && focused) setFocus(false); });
  const fleetMount = document.getElementById('fleet');
  const cards = new Map();

  store.onChange((state) => {
    const selected = store.selectedRider();
    header.update(state);
    eventLog.update(state.events);
    if (!selected) return;
    video.update(state.board, selected);
    primary.update(selected);
    // One card per rider (boards that register later get theirs on first message);
    // the rider currently shown large is hidden from the fleet column.
    state.order.forEach((id) => {
      if (!cards.has(id)) {
        const card = createRiderCard(config, { onSelect: (riderId) => { store.select(riderId); pollBoard(); } });
        cards.set(id, card);
        fleetMount.append(card.node);
      }
      const card = cards.get(id);
      card.node.hidden = id === selected.id;
      if (!card.node.hidden) card.update(state.riders.get(id));
    });
  });

  const loadSnapshot = async () => store.loadSnapshot(await api.state(config.history_seconds));

  const pollBoard = async () => {
    const rider = store.selectedRider();
    if (!rider) return;
    let board;
    try { board = await api.boardStatus(rider.id); }
    catch (e) { board = { reachable: false, error: '平台伺服器無回應' }; }
    if (store.selectedRider().id === rider.id) store.setBoard(board);  // ignore answers for a rider we've left
  };

  if (ONCE) {
    await loadSnapshot();
    const pick = new URLSearchParams(location.search).get('rider');
    if (pick) store.select(pick);
    store.setConnection('live');
    await pollBoard();
    return;
  }

  api.subscribe({
    onOpen: async () => { await loadSnapshot(); store.setConnection('live'); },  // also refills gaps after a reconnect
    onError: () => store.setConnection('lost'),
    onRider: store.applyRider,
    onEvent: store.addEvent,
  });

  pollBoard();
  setInterval(pollBoard, BOARD_POLL_MS);
  const pollSources = async () => {
    try { store.setSources((await api.sources()).sources); } catch (e) { /* backend down: the SSE pill says so */ }
  };
  pollSources();
  setInterval(pollSources, 3000);

  // "updated N s ago" and the scrolling time axis keep moving between messages
  setInterval(() => {
    const now = Date.now() / 1000;
    store.state.riders.forEach((r) => { if (r.received_at != null) r.age_sec = now - r.received_at; });
    store.setBoard(store.state.board);
  }, AGE_TICK_MS);
}

try {
  // ?theme=dark|light pins the theme from the URL (handy for the projector bookmark)
  const saved = new URLSearchParams(location.search).get('theme') || localStorage.getItem('theme');
  if (saved) document.documentElement.dataset.theme = saved;
} catch (e) { /* private mode */ }

start().catch((err) => {
  document.getElementById('header').textContent = `無法連上平台伺服器：${err.message}`;
});
