// Where is the rider on the route? Pure geometry, no DOM, no network.
//
//   const tracker = createProgressTracker(route);   // route from GET /api/app/{id}/route
//   tracker.locate({lat, lng}) -> {
//     alongM, offRouteM,            distance travelled along the line / distance away from it
//     next: { step, index, distanceM, stretchM } | null,   the next manoeuvre worth announcing; stretchM = length of the road leading to it
//     stopIndex, toStopM, toStopS,  the stop being driven to (leg index) and what is left to it
//     remainingM,                   to the end of the whole route
//   }
//
// Routes here fold back on themselves (U-turns on 光復路 put both directions a few
// metres apart), so "nearest point on the line" alone would jump between the two
// passes. The search therefore prefers the stretch just ahead of the last fix
// and only looks at the whole line when that stretch is clearly wrong.

const WINDOW_BACK_M = 60;
const WINDOW_AHEAD_M = 500;
const WINDOW_TRUST_M = 45;     // a match this close inside the window wins over a closer one elsewhere
const PASSED_MARGIN_M = 8;     // a manoeuvre this far behind counts as done
const VERTEX_MATCH_M = 6;      // manoeuvre locations are route vertices; allow for rounding

const RAD = Math.PI / 180;

// Local flat projection around `origin`: fine at city scale, and makes point-to-segment maths trivial.
function toXY(origin, [lat, lng]) {
  return [(lng - origin[1]) * RAD * 6371000 * Math.cos(origin[0] * RAD), (lat - origin[0]) * RAD * 6371000];
}

export function createProgressTracker(route) {
  const origin = route.geometry[0];
  const points = route.geometry.map((p) => toXY(origin, p));
  const cumulative = [0];
  for (let i = 1; i < points.length; i += 1) {
    cumulative.push(cumulative[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]));
  }
  const totalM = cumulative[cumulative.length - 1];

  // Pin every manoeuvre to a distance along the line, walking forward so a
  // street that is driven twice gets its two manoeuvres in the right order.
  let from = 0;
  const steps = route.steps.map((step) => {
    const target = toXY(origin, step.location);
    let best = from;
    let bestD = Infinity;
    for (let i = from; i < points.length; i += 1) {
      const d = Math.hypot(points[i][0] - target[0], points[i][1] - target[1]);
      if (d < bestD) { best = i; bestD = d; }
      if (d <= VERTEX_MATCH_M) break;
    }
    from = best;
    return { ...step, alongM: cumulative[best] };
  });
  const stops = steps.filter((s) => s.type === 'arrive');  // one per leg, in order

  let lastAlong = 0;

  function project(xy, lo, hi) {
    let best = null;
    for (let i = 1; i < points.length; i += 1) {
      if (cumulative[i] < lo || cumulative[i - 1] > hi) continue;
      const [ax, ay] = points[i - 1];
      const [bx, by] = points[i];
      const lengthSq = (bx - ax) ** 2 + (by - ay) ** 2;
      const t = lengthSq === 0 ? 0 : Math.min(1, Math.max(0, ((xy[0] - ax) * (bx - ax) + (xy[1] - ay) * (by - ay)) / lengthSq));
      const d = Math.hypot(xy[0] - (ax + t * (bx - ax)), xy[1] - (ay + t * (by - ay)));
      if (!best || d < best.offRouteM) best = { offRouteM: d, alongM: cumulative[i - 1] + t * Math.sqrt(lengthSq) };
    }
    return best;
  }

  return {
    totalM,

    // [lat, lng] at a distance along the line (used by the demo-ride simulator).
    pointAt(alongM) {
      const target = Math.min(Math.max(alongM, 0), totalM);
      let i = 1;
      while (i < cumulative.length - 1 && cumulative[i] < target) i += 1;
      const span = cumulative[i] - cumulative[i - 1] || 1;
      const t = (target - cumulative[i - 1]) / span;
      const a = route.geometry[i - 1];
      const b = route.geometry[i];
      return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
    },

    stopAlong: (legIndex) => (stops[legIndex] ? stops[legIndex].alongM : totalM),

    locate(position) {
      const xy = toXY(origin, [position.lat, position.lng]);
      const near = project(xy, lastAlong - WINDOW_BACK_M, lastAlong + WINDOW_AHEAD_M);
      const hit = near && near.offRouteM <= WINDOW_TRUST_M ? near : project(xy, 0, totalM);
      lastAlong = hit.alongM;

      const stopIndex = Math.max(0, stops.findIndex((s) => s.alongM > hit.alongM - PASSED_MARGIN_M));
      const stop = stops[stopIndex] || stops[stops.length - 1];
      // "depart" says nothing a rider needs; the first real instruction is the first turn.
      const index = steps.findIndex((s) => s.type !== 'depart' && s.alongM > hit.alongM - PASSED_MARGIN_M);
      const toStopM = Math.max(0, stop.alongM - hit.alongM);
      return {
        alongM: hit.alongM,
        offRouteM: hit.offRouteM,
        next: index < 0 ? null : {
          step: steps[index], index, distanceM: Math.max(0, steps[index].alongM - hit.alongM),
          stretchM: index > 0 ? steps[index - 1].distance_m : 0,
        },
        stopIndex,
        toStopM,
        toStopS: totalM > 0 ? route.duration_s * (toStopM / totalM) : 0,
        remainingM: Math.max(0, totalM - hit.alongM),
      };
    },
  };
}
