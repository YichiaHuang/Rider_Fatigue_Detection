// Decides when the route needs refreshing. The state is polled every second,
// the route is not: it is fetched when the order (or its stage) changes, when a
// first GPS fix arrives, and when the rider has left the line (re-route) — the
// backend's routing service is a shared, rate-limited resource
// (server/sources/routing.py). Progress ALONG the route needs no request at all:
// nav/progress.js works that out on the phone.

const OFF_ROUTE_M = 60;
const MIN_INTERVAL_MS = 8000;

export function createRouteSync({ fetchRoute, onRoute }) {
  let key = null;        // `${order id}:${order state}` the current route belongs to
  let origin = null;     // rider position the last request was made from
  let requestedAt = 0;
  let inFlight = false;

  return {
    // progress: nav/progress.js locate() for the current route | null
    observe(state, progress) {
      const order = state.app && (state.app.order || state.app.offer);
      if (!order || !order.pickup_pos || !order.dropoff_pos) {
        if (key != null) { key = null; origin = null; onRoute(null); }
        return;
      }
      const position = state.position;
      const nextKey = `${order.id}:${order.state}`;
      const changed = nextKey !== key;
      const firstFix = Boolean(position) && !origin;  // the route so far is shop -> customer only: ask again at once
      const offRoute = Boolean(position && origin && progress && progress.offRouteM > OFF_ROUTE_M);
      if (inFlight || !(changed || firstFix || (offRoute && Date.now() - requestedAt > MIN_INTERVAL_MS))) return;
      if (changed) onRoute(null);  // never draw the previous order's line under the new order
      key = nextKey;
      origin = position;
      requestedAt = Date.now();
      inFlight = true;
      fetchRoute(state.riderId, position)
        .then((route) => { if (`${route.order_id}:${route.order_state}` === key) onRoute(route); })
        .catch(() => {})  // no route: the map still shows the markers
        .finally(() => { inFlight = false; });
    },
  };
}
