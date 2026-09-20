// Fatigue level shown to the rider, derived from the platform's state. Pure
// function, no DOM — the one place that decides "normal / warning / paused".
//
//   paused   the platform's circuit breaker has paused new orders (score >= pause, until <= resume)
//   warning  score >= warn_threshold; holds until the score is at least 2 points
//            below that line (or under resume_threshold, whichever is lower), so a
//            score hovering around the line doesn't make the phone chime on and off
//   unknown  no trustworthy signal (board offline, no face, camera fault) — and
//            the platform has not paused the rider
//   normal   everything else

export const LEVEL_NORMAL = 'normal';
export const LEVEL_WARNING = 'warning';
export const LEVEL_PAUSED = 'paused';
export const LEVEL_UNKNOWN = 'unknown';

export function deriveLevel(appState, config, previousLevel) {
  if (!appState || !config) return LEVEL_UNKNOWN;
  if (appState.dispatch_blocked === 'fatigue') return LEVEL_PAUSED;
  const rider = appState.rider;
  if (rider.status === 'unknown' || rider.score == null) return LEVEL_UNKNOWN;
  if (rider.score >= config.warn_threshold) return LEVEL_WARNING;
  const clearBelow = Math.min(config.resume_threshold, config.warn_threshold - 2);
  if (previousLevel === LEVEL_WARNING && rider.score > clearBelow) return LEVEL_WARNING;
  return LEVEL_NORMAL;
}
