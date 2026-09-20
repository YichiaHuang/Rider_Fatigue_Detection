// Alert tones, synthesised with Web Audio — no audio files to ship or cache.
//
// Browsers only allow sound after a user gesture: unlock() must be called from
// a tap (the "開始使用" button does it). iOS also mutes Web Audio while the
// ringer switch is on silent; the voice channel is the fallback there.

let context = null;

// [frequency Hz, start sec, duration sec]; each pattern is short so it never masks traffic noise for long.
const PATTERNS = {
  offer: { wave: 'sine', gain: 0.5, notes: [[660, 0, 0.12], [880, 0.14, 0.12], [1175, 0.28, 0.2]] },
  warning: { wave: 'sine', gain: 0.6, notes: [[880, 0, 0.28], [660, 0.32, 0.4]] },
  paused: {
    wave: 'square', gain: 0.35,
    notes: [[988, 0, 0.16], [988, 0.24, 0.16], [988, 0.48, 0.16], [740, 0.8, 0.45]],
  },
  resumed: { wave: 'sine', gain: 0.5, notes: [[523, 0, 0.14], [659, 0.16, 0.14], [784, 0.32, 0.3]] },
  notice: { wave: 'sine', gain: 0.35, notes: [[440, 0, 0.25]] },
};

export const sound = {
  supported: () => Boolean(window.AudioContext || window.webkitAudioContext),

  unlock() {
    if (!this.supported()) return;
    context = context || new (window.AudioContext || window.webkitAudioContext)();
    context.resume().catch(() => {});
    // A silent blip inside the gesture is what actually unlocks iOS.
    const source = context.createBufferSource();
    source.buffer = context.createBuffer(1, 1, 22050);
    source.connect(context.destination);
    source.start(0);
  },

  play(name) {
    const pattern = PATTERNS[name];
    if (!context || !pattern) return;
    context.resume().catch(() => {});  // the OS suspends it whenever the app was in the background
    const t0 = context.currentTime + 0.03;
    pattern.notes.forEach(([freq, at, length]) => {
      const osc = context.createOscillator();
      const gain = context.createGain();
      osc.type = pattern.wave;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, t0 + at);
      gain.gain.linearRampToValueAtTime(pattern.gain, t0 + at + 0.015);  // no click at note start/end
      gain.gain.setValueAtTime(pattern.gain, t0 + at + length - 0.03);
      gain.gain.linearRampToValueAtTime(0, t0 + at + length);
      osc.connect(gain).connect(context.destination);
      osc.start(t0 + at);
      osc.stop(t0 + at + length + 0.02);
    });
  },
};
