// Spoken alerts (Web Speech API). A rider can't read a screen in traffic, so the
// voice carries the full message; the tone before it only gets attention.

let zhVoice = null;
let alertBusyUntil = 0;  // while a fatigue alert is being spoken, navigation guidance keeps quiet
const SECONDS_PER_CHAR = 0.28;  // rough zh-TW speaking pace, for the estimate above

function pickVoice() {
  const voices = window.speechSynthesis.getVoices();
  zhVoice = voices.find((v) => /^zh[-_]TW/i.test(v.lang))
    || voices.find((v) => /^zh/i.test(v.lang)) || null;
}

export const voice = {
  supported: () => 'speechSynthesis' in window,

  // Call from a tap: iOS refuses to speak until one utterance came from a gesture.
  unlock() {
    if (!this.supported()) return;
    pickVoice();
    window.speechSynthesis.addEventListener('voiceschanged', pickVoice);  // Android loads voices late
    const blank = new SpeechSynthesisUtterance(' ');
    blank.volume = 0;
    window.speechSynthesis.speak(blank);
  },

  // Alerts: always spoken, replacing whatever is being said.
  speak(text) {
    if (!this.supported() || !text) return;
    alertBusyUntil = Date.now() + (1.5 + text.length * SECONDS_PER_CHAR) * 1000;
    this.utter(text);
  },

  // Navigation guidance: dropped, not queued, if an alert is talking — a turn
  // instruction that arrives ten seconds late is worse than none.
  speakIfFree(text) {
    if (!this.supported() || !text || Date.now() < alertBusyUntil) return;
    this.utter(text);
  },

  utter(text) {
    window.speechSynthesis.cancel();  // a newer sentence replaces an older one, never queues behind it
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'zh-TW';
    if (zhVoice) utterance.voice = zhVoice;
    utterance.rate = 1.0;
    utterance.volume = 1.0;
    window.speechSynthesis.speak(utterance);
  },

  stop() { if (this.supported()) window.speechSynthesis.cancel(); },
};
