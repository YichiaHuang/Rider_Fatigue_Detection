// System notifications, shown through the service worker so they also work when
// the app is installed and sitting behind the navigation app.
//
// Needs a secure context (HTTPS or localhost) — on plain http://<LAN ip> this
// channel reports itself unavailable and the others still work. iOS: only after
// "加入主畫面" (iOS 16.4+). See docs/APP.md.
//
// These are LOCAL notifications: the page must still be alive to raise them.
// Waking a phone whose browser was killed needs Web Push, which is listed as
// future work in docs/APP.md.

let registration = null;

export const notify = {
  supported: () => window.isSecureContext && 'Notification' in window && 'serviceWorker' in navigator,
  permission: () => ('Notification' in window ? Notification.permission : 'unsupported'),

  async register() {
    if (!window.isSecureContext || !('serviceWorker' in navigator)) return null;
    try {
      registration = await navigator.serviceWorker.register('sw.js');
    } catch (e) { registration = null; }
    return registration;
  },

  // Call from a tap; browsers ignore permission prompts that don't come from one.
  async requestPermission() {
    if (!this.supported()) return 'unsupported';
    if (Notification.permission === 'default') {
      try { await Notification.requestPermission(); } catch (e) { /* old Safari: callback form only */ }
    }
    return Notification.permission;
  },

  async show(title, { body = '', tag = 'rider-app', urgent = false } = {}) {
    if (!this.supported() || Notification.permission !== 'granted' || !registration) return;
    try {
      await registration.showNotification(title, {
        body, tag, renotify: true, requireInteraction: urgent, icon: 'icons/icon-192.png',
        badge: 'icons/icon-192.png', lang: 'zh-Hant',
      });
    } catch (e) { /* notification quota / OS-level block: other channels already fired */ }
  },

  async clear(tag) {
    if (!registration) return;
    try {
      (await registration.getNotifications({ tag })).forEach((n) => n.close());
    } catch (e) { /* not supported on this browser */ }
  },
};
