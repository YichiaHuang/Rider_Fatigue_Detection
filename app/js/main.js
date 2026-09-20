// Wiring only: api -> store -> screens + alert engine. No rendering or fetch logic here.
import { createAlertEngine } from './alerts/alertEngine.js';
import { haptics } from './alerts/haptics.js';
import { notify } from './alerts/notify.js';
import { sound } from './alerts/sound.js';
import { voice } from './alerts/voice.js';
import { wakeLock } from './alerts/wakeLock.js';
import { api } from './api/client.js';
import { geo } from './geo/position.js';
import { createRideSimulator } from './geo/rideSimulator.js';
import { createGuidance } from './nav/guidance.js';
import { createProgressTracker } from './nav/progress.js';
import { LOST_AFTER_MS, POLL_MS, settings } from './config.js';
import { createAlertOverlay } from './screens/alertOverlay.js';
import { createHome } from './screens/home.js';
import { createLogin } from './screens/login.js';
import { createNavigation } from './screens/navigation.js';
import { createOrderMap } from './screens/orderMap.js';
import { createSettings } from './screens/settings.js';
import { createRouteSync } from './state/routeSync.js';
import { createStore } from './state/store.js';
import { NAV_TEXT } from './utils/text.js';

const params = new URLSearchParams(location.search);
// ?hold=5000 keeps the window "loading" so a headless --screenshot waits for data (same trick as the dashboard's ?once).
if (params.has('hold')) {
  const hold = new Image();
  hold.src = `/api/debug/delay?ms=${Number(params.get('hold')) || 3000}`;
  hold.hidden = true;
  document.body.append(hold);
}

// What this phone + this connection can actually do; shown on the login and settings screens.
function channelSupport() {
  const secure = window.isSecureContext;
  const permission = notify.permission();
  return [
    { key: 'sound', name: '提示音', ok: sound.supported(), note: sound.supported() ? '請把媒體音量開大' : '這個瀏覽器不支援' },
    { key: 'voice', name: '語音播報', ok: voice.supported(), note: voice.supported() ? '中文語音唸出提醒與新訂單' : '這個瀏覽器不支援' },
    { key: 'vibration', name: '震動', ok: haptics.supported(), note: haptics.supported() ? '' : 'iPhone 的瀏覽器不支援震動' },
    { key: 'notification', name: '系統通知', ok: notify.supported() && permission !== 'denied',
      note: !secure ? '需要 HTTPS 網址' : permission === 'denied' ? '已在系統設定中被封鎖'
        : !notify.supported() ? 'iPhone 請先「加入主畫面」再開啟' : permission === 'granted' ? '已允許' : '開始使用時會詢問' },
    { key: 'location', name: '定位與導航', ok: geo.supported() || settings.demoRide,
      note: settings.demoRide ? '模擬騎乘已開啟：位置是模擬的' : geo.supported() ? '上線時顯示你的位置，可在 App 內導航'
        : '需要 HTTPS 網址；可到設定開啟「模擬騎乘」示範導航' },
  ];
}

const toastNode = document.getElementById('toast');
let toastTimer = null;
function toast(message) {
  toastNode.textContent = message;
  toastNode.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastNode.hidden = true; }, 3500);
}

async function start() {
  const store = createStore();
  // Not awaited: on iOS the registration can take seconds (or never settle);
  // the screen must not wait for it. Notifications simply start working once it resolves.
  notify.register();

  let home = null;  // built on first sign-in: it needs the thresholds from /api/config
  let pollTimer = null;
  let lastOkAt = 0;

  const engine = createAlertEngine(store, {
    onAcknowledge: (level) => api.acknowledge(store.state.riderId, level).catch(() => {}),  // best effort
  });

  // Every screen action goes through here: apply the state the server answered with.
  async function act(call) {
    try {
      store.applyAppState(await call(store.state.riderId));
    } catch (e) {
      if (e.state) store.applyAppState(e.state);
      toast(e.name === 'AbortError' ? '平台沒有回應，請再試一次' : e.message);
    }
  }

  const routeSync = createRouteSync({ fetchRoute: api.route, onRoute: (route) => store.setRoute(route) });
  const guidance = createGuidance();
  const orderMap = createOrderMap();  // one map for the whole app; the order card and the navigation screen take turns holding it
  const simulator = createRideSimulator((position) => store.setPosition(position));
  let tracker = null;        // nav/progress.js for the current route
  let trackedRoute = null;
  let autoNavigated = false;

  const navigation = createNavigation(document.getElementById('nav'), orderMap, {
    onClose: () => store.setNavigating(false),
    onRefresh: () => store.setPosition(store.state.position),  // re-render now that the map follows again
    onAdvance: () => act((id) => api.advanceOrder(id, store.state.app.order.id)),
  });

  const overlay = createAlertOverlay(document.getElementById('alert'), { onAcknowledge: () => engine.acknowledge() });
  const settingsSheet = createSettings(document.getElementById('settings'), {
    channelSupport,
    onTest: (level) => engine.test(level),
    onEnableNotifications: () => notify.requestPermission(),
    onSignOut: signOut,
    onDemoRideChange: () => store.setPosition(null),  // drop the old source's last fix; the new source reports on the next change
  });
  const login = createLogin(document.getElementById('screen-login'), {
    loadRiders: api.riders,
    channelSupport,
    onStart: (riderId) => {
      // Still inside the tap: the only moment browsers let us unlock these.
      sound.unlock();
      voice.unlock();
      if (settings.channels.sound) sound.play('resumed');
      notify.requestPermission();
      signIn(riderId);
    },
  });

  store.onChange((state) => {
    if (home) home.update(state);
    overlay.update(state);
    engine.observe(state);

    // ?nav=1 opens navigation by itself once there is a delivery (demo bookmark, screenshots).
    if (params.has('nav') && !autoNavigated && state.app && state.app.order) { autoNavigated = true; store.setNavigating(true); }
    // Navigation. Delivered (or signed out) ends it; "已取餐" keeps it open and the route to the customer follows.
    if (state.navigating && !(state.app && state.app.order)) store.setNavigating(false);
    if (state.route !== trackedRoute) {
      trackedRoute = state.route;
      // Progress and guidance only make sense on a line that starts at the rider.
      tracker = state.route && state.route.from_rider ? createProgressTracker(state.route) : null;
      simulator.setRoute(tracker);
    }
    const progress = tracker && state.position ? tracker.locate(state.position) : null;
    routeSync.observe(state, progress);
    navigation.update(state, progress);
    const order = state.app && state.app.order;
    guidance.observe({
      navigating: state.navigating, route: tracker ? state.route : null, progress, tripKey: order ? `${order.id}:${order.state}` : null,
      stopNames: state.route ? state.route.legs.map((leg) => NAV_TEXT.stops[leg.to]) : [],
    });

    // Position source: the phone's GPS, or the demo ride — never both.
    const working = Boolean(state.app && (state.app.on_duty || state.app.order));
    const simulate = settings.demoRide;
    wakeLock.set(working);
    geo.watch(working && !simulate, (position) => store.setPosition(position));
    if (working && simulate) simulator.start(); else simulator.stop();
    simulator.setRiding(state.navigating);
  });

  async function poll() {
    const riderId = store.state.riderId;
    try {
      const appState = await api.state(riderId);
      lastOkAt = Date.now();
      store.setConnection('live');
      store.applyAppState(appState);
    } catch (e) {
      if (e.status === 404) { toast('平台上找不到這位騎手，請重新選擇'); return signOut(); }
      if (Date.now() - lastOkAt > LOST_AFTER_MS) store.setConnection('lost');
    }
    if (store.state.riderId === riderId) pollTimer = setTimeout(poll, POLL_MS);
  }

  async function signIn(riderId) {
    settings.riderId = riderId;
    if (!store.state.config) {
      try { store.setConfig(await api.config()); } catch (e) { login.show(); return toast(`連不上平台伺服器（${e.message}）`); }
    }
    const homeMount = document.getElementById('screen-home');
    home = home || createHome(homeMount, store.state.config, orderMap, {
      onSetDuty: (on) => act((id) => api.setDuty(id, on)),
      onAnswerOffer: (orderId, accept) => act((id) => api.answerOffer(id, orderId, accept)),
      onAdvance: (orderId) => act((id) => api.advanceOrder(id, orderId)),
      onOpenSettings: () => settingsSheet.open(),
      onStartNavigation: () => { orderMap.recenter(); store.setNavigating(true); },
    });
    store.setRider(riderId);
    store.setConnection('connecting');
    lastOkAt = Date.now();
    login.hide();
    home.show();
    clearTimeout(pollTimer);
    poll();
  }

  function signOut() {
    clearTimeout(pollTimer);
    settings.riderId = null;
    store.setRider(null);
    wakeLock.set(false);
    geo.watch(false, (position) => store.setPosition(position));
    simulator.stop();
    if (home) home.hide();
    login.show();
  }

  // Coming back from the navigation app: don't wait for the next timer tick.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && store.state.riderId) { clearTimeout(pollTimer); poll(); }
  });

  // Returning rider: skip the picker. Sound stays locked until the first tap
  // anywhere (browser rule), so unlock on it. ?rider=<id> does the same from a bookmark.
  const remembered = params.get('rider') || settings.riderId;
  if (remembered) {
    document.addEventListener('pointerdown', () => { sound.unlock(); voice.unlock(); }, { once: true });
    signIn(remembered);
  } else {
    login.show();
  }
}

start().then(() => { window.__riderAppStarted = true; }, (e) => { throw e; });
