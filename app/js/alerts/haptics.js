// Vibration. Android only — iOS Safari has no Vibration API, so there the
// sound / voice / notification channels carry the alert.

const PATTERNS = {
  offer: [120, 80, 120],
  warning: [300, 150, 300],
  paused: [600, 200, 600, 200, 600],
  resumed: [150],
  notice: [200],
};

export const haptics = {
  supported: () => 'vibrate' in navigator,
  play(name) {
    if (!this.supported() || !PATTERNS[name]) return;
    try { navigator.vibrate(PATTERNS[name]); } catch (e) { /* blocked before the first tap */ }
  },
  stop() { if (this.supported()) try { navigator.vibrate(0); } catch (e) { /* ignore */ } },
};
