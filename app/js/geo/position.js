// The phone's GPS, only while it is useful (on duty or delivering): it costs
// battery, and a rider's whereabouts are nobody's business when they are off.
// Secure context only (HTTPS / localhost), like notifications.

const MIN_MOVE_M = 10;  // GPS jitter below this would only cause re-renders

let watchId = null;
let last = null;

export function distanceM(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

export const geo = {
  supported: () => window.isSecureContext && 'geolocation' in navigator,

  // onPosition({lat, lng, accuracy}) on real movement; onPosition(null) when the fix is lost or refused.
  watch(on, onPosition) {
    if (!this.supported()) return;
    if (!on) {
      if (watchId != null) navigator.geolocation.clearWatch(watchId);
      if (watchId != null || last) onPosition(null);
      watchId = null;
      last = null;
      return;
    }
    if (watchId != null) return;
    watchId = navigator.geolocation.watchPosition(
      (fix) => {
        const position = { lat: fix.coords.latitude, lng: fix.coords.longitude, accuracy: fix.coords.accuracy };
        if (last && distanceM(last, position) < MIN_MOVE_M) return;
        last = position;
        onPosition(position);
      },
      () => { last = null; onPosition(null); },  // denied / no signal: the map still shows pickup -> drop-off
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
  },
};
