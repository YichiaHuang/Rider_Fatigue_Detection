// Demo ride: a stand-in for the phone's GPS that travels along the current
// route, so in-app navigation can be shown on a table in the venue. Switched on
// in Settings ("模擬騎乘") or with ?demo_ride=1; while it is on, the real GPS is
// not read at all. The app shows "模擬定位" whenever this is the position source.
//
// It rides to the end of the current leg and waits there — the rider still has
// to tap "已取餐" / "已送達", exactly as on a real delivery.

const SPEED_MPS = 18;      // ~65 km/h: faster than real so a 3 km trip fits in a demo
const TICK_MS = 500;
// Where the simulated rider waits for orders: 陽明交大 光復校區.
export const DEMO_START = { lat: 24.7869, lng: 120.9975, accuracy: 8, simulated: true };

export function createRideSimulator(onPosition) {
  let tracker = null;   // nav/progress.js tracker of the route being ridden
  let alongM = 0;
  let riding = false;
  let timer = null;
  let current = null;

  function emit(lat, lng) {
    current = { lat, lng, accuracy: 8, simulated: true };
    onPosition(current);
  }

  function tick() {
    if (!tracker || !riding) return;
    const stopAt = tracker.stopAlong(0);  // every route starts at the rider, so leg 0 is always "the next stop"
    if (alongM >= stopAt) return;
    alongM = Math.min(stopAt, alongM + SPEED_MPS * (TICK_MS / 1000));
    emit(...tracker.pointAt(alongM));
  }

  return {
    start() {
      if (timer) return;
      if (!current) emit(DEMO_START.lat, DEMO_START.lng);
      timer = setInterval(tick, TICK_MS);
    },
    stop() {
      clearInterval(timer);
      timer = null;
      current = null;  // the route is kept: switching the demo ride back on resumes on the same trip
    },
    // A new route always begins at the simulated rider's own position, so start from its beginning.
    setRoute(nextTracker) { tracker = nextTracker; alongM = 0; },
    setRiding(on) { riding = on; },
  };
}
