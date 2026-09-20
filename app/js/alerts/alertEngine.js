// Decides WHEN the rider is alerted and through WHICH channels. It only reacts
// to changes (level transitions, a new offer), never to the steady state, so a
// rider who stays tired is not nagged every second.
//
//   normal  -> warning   amber overlay (closes itself), two-tone chime, short voice line
//   *       -> paused    red overlay until acknowledged, alarm tone x3 over ~12 s, voice, long vibration
//   paused  -> *         "已恢復派單" chime + voice
//   known   -> unknown   one soft notice while on duty (the detector stopped watching)
//   new offer            arpeggio + the order read aloud, so accepting doesn't require reading
//
// Design note (plan.md): the intervention is pausing dispatch; these alerts tell
// the rider about it. They are deliberately short and non-startling — an alarm
// that frightens someone riding in traffic would be its own hazard.

import { settings } from '../config.js';
import { LEVEL_NORMAL, LEVEL_PAUSED, LEVEL_UNKNOWN, LEVEL_WARNING } from '../state/alertLevel.js';
import { ALERT_TEXT, fillRules, offerSpeech } from '../utils/text.js';
import { haptics } from './haptics.js';
import { notify } from './notify.js';
import { sound } from './sound.js';
import { voice } from './voice.js';

const VOICE_DELAY_MS = 1300;         // let the tone finish before speaking
const WARNING_AUTOCLOSE_MS = 12000;  // a warning must not need a tap while riding
const PAUSED_REPEAT_MS = 6000;
const PAUSED_REPEATS = 3;
const FATIGUE_TAG = 'fatigue';
const OFFER_TAG = 'offer';

export function createAlertEngine(store, { onAcknowledge }) {
  let previousLevel = null;
  let previousOfferId = null;
  let timers = [];

  const later = (fn, ms) => timers.push(setTimeout(fn, ms));
  function clearTimers() { timers.forEach(clearTimeout); timers = []; }

  // One alert, fanned out to whichever channels this rider left switched on.
  function fire(pattern, { speech, title, body, tag, urgent = false, notification = true }) {
    const on = settings.channels;
    if (on.sound) sound.play(pattern);
    if (on.vibration) haptics.play(pattern);
    if (on.voice && speech) later(() => voice.speak(speech), on.sound ? VOICE_DELAY_MS : 0);
    if (on.notification && notification) notify.show(title, { body, tag, urgent });
  }

  function raise(level, hasOrder, isTest = false) {
    clearTimers();
    const text = ALERT_TEXT[level];
    const withOrder = level === LEVEL_PAUSED && hasOrder;
    const body = fillRules(withOrder ? text.bodyWithOrder : text.body, store.state.config);
    const speech = withOrder ? text.voiceWithOrder : text.voice;
    store.setAlert({ level, hasOrder: withOrder, isTest });
    fire(level, { speech, title: text.title, body, tag: FATIGUE_TAG, urgent: level === LEVEL_PAUSED });
    if (level === LEVEL_WARNING) {
      later(() => { if (store.state.alert && store.state.alert.level === LEVEL_WARNING) store.setAlert(null); },
        WARNING_AUTOCLOSE_MS);
      return;
    }
    for (let i = 1; i < PAUSED_REPEATS; i += 1) {
      later(() => fire(level, { speech: i === PAUSED_REPEATS - 1 ? speech : null, notification: false }),
        i * PAUSED_REPEAT_MS);
    }
  }

  function silence() {
    clearTimers();
    voice.stop();
    haptics.stop();
  }

  return {
    // Called on every store change; cheap when nothing changed.
    observe(state) {
      if (!state.app) { previousLevel = null; previousOfferId = null; return; }
      const level = state.level;
      const from = previousLevel == null ? LEVEL_NORMAL : previousLevel;  // app opened while tired: alert too
      const hasOrder = Boolean(state.app.order);

      if (level !== from) {
        if (level === LEVEL_PAUSED) raise(LEVEL_PAUSED, hasOrder);
        else if (from === LEVEL_PAUSED) {
          silence();
          store.setAlert(null);
          notify.clear(FATIGUE_TAG);
          const text = ALERT_TEXT.resumed;
          fire('resumed', { speech: text.voice, title: text.title, body: text.body, tag: FATIGUE_TAG });
        } else if (level === LEVEL_WARNING && from === LEVEL_NORMAL) raise(LEVEL_WARNING, hasOrder);
        else if (level === LEVEL_UNKNOWN && previousLevel != null && state.app.on_duty) {
          const text = ALERT_TEXT.signalLost;
          fire('notice', { speech: text.voice, title: text.title, tag: FATIGUE_TAG, notification: false });
        }
        if (level === LEVEL_NORMAL && store.state.alert && store.state.alert.level === LEVEL_WARNING) {
          store.setAlert(null);
        }
      }
      previousLevel = level;

      const offer = state.app.offer;
      if (offer && offer.id !== previousOfferId) {
        fire('offer', {
          speech: offerSpeech(offer), title: `${ALERT_TEXT.offer.title}：${offer.restaurant}`,
          body: `${offer.distance_km} 公里｜$${offer.fee}｜送到 ${offer.dropoff}`, tag: OFFER_TAG,
          notification: document.visibilityState !== 'visible',  // on screen already when the app is in front
        });
      }
      if (!offer && previousOfferId) notify.clear(OFFER_TAG);
      previousOfferId = offer ? offer.id : null;
    },

    acknowledge() {
      const alert = store.state.alert;
      if (!alert) return;
      silence();
      store.setAlert(null);
      notify.clear(FATIGUE_TAG);
      if (!alert.isTest) onAcknowledge(alert.level);  // a rehearsal doesn't belong in the platform's event log
    },

    // Settings screen: let the rider hear what an alert is like before they need one.
    test(level) { raise(level, false, true); },
  };
}
