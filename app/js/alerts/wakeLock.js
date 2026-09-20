// Keep the screen on while the rider is on duty (phone on the handlebar mount).
// A page whose screen turned off gets throttled by the OS and would miss
// alerts, so this is part of the alert path, not a nicety. Secure context only.

let sentinel = null;
let wanted = false;

async function acquire() {
  if (!wanted || sentinel || !('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
  try {
    sentinel = await navigator.wakeLock.request('screen');
    sentinel.addEventListener('release', () => { sentinel = null; });
  } catch (e) { sentinel = null; }  // battery saver, or not a secure context
}

// The OS drops the lock whenever the app goes to the background; take it back on return.
document.addEventListener('visibilitychange', acquire);

export const wakeLock = {
  supported: () => window.isSecureContext && 'wakeLock' in navigator,
  active: () => sentinel != null,
  set(on) {
    wanted = on;
    if (on) return acquire();
    if (sentinel) sentinel.release().catch(() => {});
    sentinel = null;
    return Promise.resolve();
  },
};
