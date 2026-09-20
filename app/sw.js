// Service worker: makes the app installable, keeps the shell available when the
// network drops, and owns system notifications.
//
// Caching rule: API calls are NEVER cached (a stale fatigue score is worse than
// none). The shell is network-first, so a deploy shows up on the next launch;
// the cache is only the offline fallback. Map tiles are another origin and pass
// straight through (OpenStreetMap's tile policy forbids bulk caching anyway).

const CACHE = 'rider-app-shell-v4';
const SHELL = [
  './', 'index.html', 'manifest.webmanifest', 'css/tokens.css', 'css/app.css', 'icons/icon.svg',
  'icons/icon-192.png', 'js/main.js', 'js/config.js', 'js/api/client.js', 'js/state/store.js',
  'js/state/alertLevel.js', 'js/alerts/alertEngine.js', 'js/alerts/sound.js', 'js/alerts/voice.js',
  'js/alerts/haptics.js', 'js/alerts/notify.js', 'js/alerts/wakeLock.js', 'js/screens/login.js',
  'js/screens/home.js', 'js/screens/alertOverlay.js', 'js/screens/settings.js', 'js/utils/dom.js',
  'js/utils/text.js', 'js/geo/position.js', 'js/geo/rideSimulator.js', 'js/nav/progress.js', 'js/nav/guidance.js',
  'js/screens/navigation.js', 'js/state/routeSync.js', 'js/screens/orderMap.js',
  'vendor/leaflet/leaflet.js', 'vendor/leaflet/leaflet.css',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin || url.pathname.includes('/api/')) return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request, { ignoreSearch: true })));
});

// Tapping a fatigue / new-order notification brings the app back to the front.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      const open = clients.find((c) => c.url.startsWith(self.registration.scope));
      return open ? open.focus() : self.clients.openWindow(self.registration.scope);
    }));
});
