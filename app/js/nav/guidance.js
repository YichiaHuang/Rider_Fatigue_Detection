// Spoken turn-by-turn guidance. Decides WHEN to speak; the wording lives in
// utils/text.js. One manoeuvre is announced at most three times:
//
//   far   ~300 m   only on a long stretch, so the rider can pick a lane early
//   near  ~120 m   "前方 120 公尺右轉進入光復路二段"
//   now   ~30 m    "右轉"
//
// Guidance never talks over a fatigue alert: it uses voice.speakIfFree(), which
// stays silent while an alert sentence is being spoken (alerts/voice.js).

import { settings } from '../config.js';
import { voice } from '../alerts/voice.js';
import { NAV_TEXT, maneuverSpeech } from '../utils/text.js';

const STAGES = [
  { name: 'now', withinM: 35 },
  { name: 'near', withinM: 140 },
  { name: 'far', withinM: 330, minStretchM: 600 },
];
const ARRIVED_WITHIN_M = 35;

export function createGuidance() {
  let route = null;
  let trip = null;          // `${order id}:${order state}` — a new stage is a new trip, not a re-route
  let spoken = new Set();   // `${step index}:${stage}` already announced on this route
  let started = false;

  const say = (text) => { if (settings.channels.voice && text) voice.speakIfFree(text); };

  return {
    // Called on every store change while navigating. progress: nav/progress.js locate() | null.
    observe({ navigating, route: currentRoute, progress, stopNames, tripKey }) {
      if (!navigating || !currentRoute) { route = null; started = false; return; }
      if (tripKey !== trip) { trip = tripKey; route = null; started = false; }  // "已取餐": announce the way to the customer afresh
      if (currentRoute !== route) {
        const rerouted = route != null && started;
        route = currentRoute;
        spoken = new Set();
        if (rerouted) say(NAV_TEXT.rerouted);
      }
      if (!progress || !progress.next) return;
      const { step, index, distanceM, stretchM } = progress.next;
      const stopName = stopNames[step.leg];

      if (!started) {
        started = true;
        spoken.add(`${index}:far`);  // the opening line already covers the first manoeuvre
        say(`${NAV_TEXT.started}${maneuverSpeech(step, distanceM, stopName, 'far')}`);
        return;
      }
      if (step.type === 'arrive') {
        if (distanceM <= ARRIVED_WITHIN_M && !spoken.has(`${index}:now`)) {
          spoken.add(`${index}:now`);
          say(maneuverSpeech(step, distanceM, stopName, 'now'));
        }
        return;
      }
      const stage = STAGES.find((s) => distanceM <= s.withinM);
      if (!stage || spoken.has(`${index}:${stage.name}`)) return;
      // Reaching a closer stage makes the farther ones pointless.
      STAGES.forEach((s) => { if (s.withinM >= stage.withinM) spoken.add(`${index}:${s.name}`); });
      if (stage.minStretchM && stretchM < stage.minStretchM) return;  // short block: "near" and "now" are plenty
      say(maneuverSpeech(step, distanceM, stopName, stage.name));
    },
  };
}
