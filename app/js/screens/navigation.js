// Full-screen in-app navigation: next manoeuvre on top, the map following the
// rider, what is left to the stop and the "已取餐 / 已送達" button at the bottom.
//
// The reason this exists instead of a link to a maps app: a rider who switches
// apps puts this one in the background, where fatigue alerts cannot reach them.
// Here the alert overlay simply appears on top of the navigation.
//
// Glanceable by design — one arrow, one distance, one street name. Everything
// else is spoken (nav/guidance.js).

import { NAV_TEXT, ORDER_STEP_TEXT, fmtDistance, maneuverIcon, maneuverText } from '../utils/text.js';
import { el } from '../utils/dom.js';

export function createNavigation(mount, orderMap, actions) {
  const icon = el('div', { class: 'nav-icon', 'aria-hidden': 'true' });
  const distance = el('div', { class: 'nav-distance' });
  const instruction = el('div', { class: 'nav-instruction' });
  const notice = el('div', { class: 'nav-notice', role: 'status' });
  const mapSlot = el('div', { class: 'nav-map' });
  const recenter = el('button', { class: 'nav-recenter', type: 'button', text: '回到我的位置',
    onclick: () => { orderMap.recenter(); actions.onRefresh(); } });
  const summary = el('div', { class: 'nav-summary' });
  const stopLine = el('div', { class: 'nav-stop' });
  const advance = el('button', { class: 'btn btn-primary btn-xl grow', type: 'button' });
  let advancing = false;
  advance.addEventListener('click', async () => {
    if (advancing) return;
    advancing = true;
    advance.disabled = true;
    try { await actions.onAdvance(); } finally { advancing = false; advance.disabled = false; }
  });

  mount.append(
    el('div', { class: 'nav-banner', role: 'status', 'aria-live': 'polite' }, icon, el('div', { class: 'nav-banner-text' }, distance, instruction)),
    notice,
    mapSlot,
    recenter,
    el('div', { class: 'nav-panel' },
      el('div', { class: 'nav-panel-text' }, summary, stopLine),
      el('div', { class: 'button-row' },
        el('button', { class: 'btn btn-xl', type: 'button', text: '結束導航', onclick: actions.onClose }),
        advance)));

  return {
    // progress: nav/progress.js locate() | null (no fix yet, or no route yet)
    update(state, progress) {
      const order = state.app && state.app.order;
      const open = state.navigating && Boolean(order);
      mount.hidden = !open;
      if (!open) return;

      if (mapSlot.firstChild !== orderMap.node) mapSlot.replaceChildren(orderMap.node);
      orderMap.update({ order, route: state.route, position: state.position, follow: true });
      recenter.hidden = orderMap.isFollowing() || !state.position;

      const pickedUp = order.state === 'picked_up';
      const stopKind = pickedUp ? 'dropoff' : 'pickup';
      advance.textContent = ORDER_STEP_TEXT[order.state].action;
      stopLine.textContent = `${NAV_TEXT.stops[stopKind]}：${pickedUp ? order.dropoff : `${order.restaurant}・${order.pickup}`}`;

      const next = progress && progress.next;
      if (next) {
        const stopName = NAV_TEXT.stops[state.route.legs[next.step.leg].to];
        icon.textContent = maneuverIcon(next.step);
        distance.textContent = fmtDistance(next.distanceM);
        instruction.textContent = maneuverText(next.step, stopName);
        summary.textContent = `還有 ${fmtDistance(progress.toStopM)}・約 ${Math.max(1, Math.round(progress.toStopS / 60))} 分`;
      } else {
        icon.textContent = '…';
        distance.textContent = '';
        instruction.textContent = state.position ? '規劃路線中…' : NAV_TEXT.waitingForFix;
        summary.textContent = '';
      }

      // Anything that makes the guidance less trustworthy is said in words, on screen.
      const notes = [];
      if (state.position && state.position.simulated) notes.push(NAV_TEXT.simulated);
      if (state.route && state.route.source === 'estimate') notes.push(NAV_TEXT.estimate);
      if (!state.position) notes.push('收不到定位：請確認已允許定位，或到設定開啟「模擬騎乘」');
      notice.textContent = notes.join('｜');
      notice.hidden = notes.length === 0;
    },
  };
}
