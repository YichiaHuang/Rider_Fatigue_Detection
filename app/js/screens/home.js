// Main screen: fatigue gauge, duty switch, the current offer / order, today's
// totals. Renders from the store only; every action is a callback from main.js.

import { CLOSED_TEXT, LEVEL_TEXT, NO_SIGNAL_TEXT, ORDER_STEP_TEXT, REASON_TEXT, REST_TIPS, fmtCountdown, fmtDuration,
  fmtMoney, fmtScore, navigationUrl, tripText, unknownReason } from '../utils/text.js';
import { el, renderIfChanged, svg } from '../utils/dom.js';

const CONNECTION_TEXT = { connecting: '連線中', live: '已連線', lost: '連線中斷' };
const GAUGE = { cx: 100, cy: 100, r: 82, length: Math.PI * 82 };

// Disable the button while its request is in flight: a double tap on "接單"
// must not fire twice, and the rider sees that the tap registered.
function actionButton(props, label, action) {
  const button = el('button', { type: 'button', ...props, text: label });
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await action(); } finally { button.disabled = false; }
  });
  return button;
}

function gaugePoint(fraction, radius) {
  const angle = Math.PI * (1 - fraction);  // 0 = left end, 1 = right end of the half circle
  return [GAUGE.cx + radius * Math.cos(angle), GAUGE.cy - radius * Math.sin(angle)];
}

function createFatigueCard(mount, config) {
  const max = config.score_display_max;
  const arc = `M ${GAUGE.cx - GAUGE.r} ${GAUGE.cy} A ${GAUGE.r} ${GAUGE.r} 0 0 1 ${GAUGE.cx + GAUGE.r} ${GAUGE.cy}`;
  const value = svg('path', { d: arc, class: 'gauge-value', 'stroke-dasharray': `0 ${GAUGE.length}` });
  const tick = (threshold, cls) => {
    const [x1, y1] = gaugePoint(threshold / max, GAUGE.r - 12);
    const [x2, y2] = gaugePoint(threshold / max, GAUGE.r + 12);
    return svg('line', { x1, y1, x2, y2, class: `gauge-tick ${cls}` });
  };
  const gauge = svg('svg', { viewBox: '0 0 200 112', class: 'gauge', role: 'img' },
    svg('path', { d: arc, class: 'gauge-track' }), value,
    tick(config.warn_threshold, 'tick-warning'), tick(config.pause_threshold, 'tick-critical'));

  const score = el('div', { class: 'gauge-score' });
  const badge = el('div', { class: 'level-badge' });
  const hint = el('p', { class: 'level-hint' });
  const reasons = el('div', { class: 'chips' });
  const legend = el('p', { class: 'fineprint',
    text: `分數 ${config.warn_threshold} 提醒｜${config.pause_threshold} 暫停派新單｜降到 ${config.resume_threshold} 恢復` });
  mount.append(el('div', { class: 'gauge-wrap' }, gauge, score), badge, hint, reasons, legend);

  return (state) => {
    const rider = state.app.rider;
    const known = state.level !== 'unknown';
    const text = LEVEL_TEXT[state.level];
    const fraction = known ? Math.min(1, Math.max(0, rider.score / max)) : 0;
    mount.dataset.level = state.level;
    value.setAttribute('stroke-dasharray', `${fraction * GAUGE.length} ${GAUGE.length}`);
    gauge.setAttribute('aria-label', `疲勞分數 ${fmtScore(rider.score)}，${text.label}`);
    // A score we can't vouch for is shown greyed and labelled, never as a live number.
    score.replaceChildren(
      el('span', { class: 'gauge-number', text: fmtScore(rider.score) }),
      el('span', { class: 'gauge-unit', text: known ? '疲勞分數' : '最後一筆分數' }));
    badge.replaceChildren(el('span', { class: 'icon', 'aria-hidden': 'true', text: text.icon }), text.label);
    hint.textContent = known ? text.hint : unknownReason(rider);
    const active = (rider.detail && rider.detail.reasons) || [];
    reasons.replaceChildren(...active.filter((r) => REASON_TEXT[r]).map((r) => el('span', { class: 'chip', text: REASON_TEXT[r] })));
  };
}

function offerCard(offer, mapNode, actions) {
  return el('article', { class: 'card order-card', data: { kind: 'offer' } },
    el('div', { class: 'order-head' },
      el('span', { class: 'order-kicker', text: '新訂單' }),
      el('span', { class: 'countdown', id: 'offer-countdown', text: '' })),
    el('h2', { class: 'order-title', text: offer.restaurant }),
    el('p', { class: 'order-items', text: offer.items }),
    mapNode,
    route(offer),
    el('div', { class: 'order-meta' },
      el('span', { class: 'fee', text: fmtMoney(offer.fee) }),
      el('span', { class: 'muted trip-info' })),
    el('div', { class: 'button-row' },
      actionButton({ class: 'btn btn-xl' }, '略過', () => actions.onAnswerOffer(offer.id, false)),
      actionButton({ class: 'btn btn-primary btn-xl grow' }, '接單', () => actions.onAnswerOffer(offer.id, true))));
}

function route(order) {
  return el('ol', { class: 'route' },
    el('li', { data: { step: 'pickup', done: String(order.state === 'picked_up') } },
      el('span', { class: 'route-label', text: '取餐' }), el('span', { text: order.pickup })),
    el('li', { data: { step: 'dropoff' } },
      el('span', { class: 'route-label', text: '送達' }), el('span', { text: order.dropoff })));
}

function activeOrderCard(order, paused, mapNode, actions) {
  const step = ORDER_STEP_TEXT[order.state];
  const nextStop = order.state === 'picked_up' ? order.dropoff_pos : order.pickup_pos;
  return el('article', { class: 'card order-card', data: { kind: 'active' } },
    el('div', { class: 'order-head' },
      el('span', { class: 'order-kicker', text: step.status }),
      el('span', { class: 'fee', text: fmtMoney(order.fee) })),
    el('h2', { class: 'order-title', text: order.restaurant }),
    el('p', { class: 'order-items', text: order.items }),
    mapNode,
    el('p', { class: 'muted trip-info' }),
    route(order),
    paused && el('p', { class: 'order-note', text: '已暫停派新單：這一單慢慢送，送完請休息。' }),
    el('div', { class: 'button-row' },
      nextStop && el('button', { class: 'btn btn-xl', type: 'button', text: '開始導航', onclick: actions.onStartNavigation }),
      actionButton({ class: 'btn btn-primary btn-xl grow' }, step.action, () => actions.onAdvance(order.id))),
    // Leaving for another app puts this one in the background, where alerts can't reach the rider — say so.
    nextStop && el('a', { class: 'btn btn-quiet nav-link', href: navigationUrl(nextStop), target: '_blank', rel: 'noopener',
      text: '改用外部導航 App（離開期間收不到疲勞提醒）' }));
}

// Both conditions must hold before orders come back; each line shows where it stands.
function restCard(config) {
  const condition = (id, label) => el('li', { class: 'condition', id },
    el('span', { class: 'condition-mark', 'aria-hidden': 'true' }),
    el('span', { class: 'condition-label', text: label }),
    el('span', { class: 'condition-value' }));
  return el('article', { class: 'card rest-card' },
    el('h2', { class: 'order-title', text: '休息一下' }),
    el('p', { text: '平台已暫停派新單。下面兩項都達成後會自動恢復，不需要任何操作。' }),
    el('ul', { class: 'conditions' },
      condition('rest-time', `至少休息 ${fmtDuration(config.min_rest_sec)}`),
      condition('rest-score', `疲勞分數降到 ${config.resume_threshold} 以下`)),
    el('ul', { class: 'tips' }, REST_TIPS.map((tip) => el('li', { text: tip }))));
}

function updateRestCard(mount, rider, config) {
  const set = (id, done, value) => {
    const row = mount.querySelector(`#${id}`);
    if (!row) return;
    row.dataset.done = String(done);
    row.querySelector('.condition-mark').textContent = done ? '✓' : '○';
    row.querySelector('.condition-value').textContent = value;
  };
  const remaining = rider.rest_remaining_sec || 0;
  set('rest-time', remaining <= 0, remaining <= 0 ? '已達成' : `還要 ${fmtCountdown(remaining)}`);
  // A score we can't see right now (no face, link lost) can't be "low enough": the breaker only resumes on a live score.
  const live = rider.status !== 'unknown' && rider.score != null;
  set('rest-score', live && rider.score <= config.resume_threshold,
    live ? `目前 ${fmtScore(rider.score)}` : unknownReason(rider) || '等待偵測訊號');
}

function noSignalCard(rider) {
  return el('article', { class: 'card idle-card no-signal-card' },
    el('p', { class: 'idle-title', text: NO_SIGNAL_TEXT.title }),
    el('p', { class: 'no-signal-reason', id: 'no-signal-reason', text: unknownReason(rider) }),
    el('p', { class: 'muted', text: NO_SIGNAL_TEXT.body }));
}

function idleCard(app) {
  const closed = app.last_closed && CLOSED_TEXT[app.last_closed.state];
  if (!app.on_duty) {
    return el('article', { class: 'card idle-card' },
      el('p', { class: 'idle-title', text: '目前下線中' }),
      el('p', { class: 'muted', text: '上線後才會收到訂單。疲勞偵測不受影響，持續運作。' }),
      app.dispatch_blocked === 'no_signal' && el('p', { class: 'muted', text: NO_SIGNAL_TEXT.offDuty }));
  }
  return el('article', { class: 'card idle-card' },
    el('div', { class: 'searching', 'aria-hidden': 'true' }, el('span'), el('span'), el('span')),
    el('p', { class: 'idle-title', text: '等待訂單中…' }),
    closed && el('p', { class: 'muted', text: closed }));
}

export function createHome(mount, config, orderMap, actions) {
  const topbar = mount.querySelector('#topbar');
  const banner = mount.querySelector('#banner');
  const dutyMount = mount.querySelector('#duty');
  const ordersMount = mount.querySelector('#orders');
  const statsMount = mount.querySelector('#stats');
  const fatigueMount = mount.querySelector('#fatigue');
  const updateFatigue = createFatigueCard(fatigueMount, config);

  const riderName = el('span', { class: 'topbar-name' });
  const connection = el('span', { class: 'pill' }, el('span', { class: 'dot' }), el('span', { class: 'pill-text' }));
  topbar.append(
    el('div', { class: 'topbar-title' }, el('strong', { text: '騎手夥伴' }), riderName),
    connection,
    el('button', { class: 'icon-button', type: 'button', 'aria-label': '設定', text: '⚙', onclick: actions.onOpenSettings }));

  return {
    show() { mount.hidden = false; },
    hide() { mount.hidden = true; },

    update(state) {
      connection.dataset.state = state.connection === 'live' ? 'ok' : state.connection === 'lost' ? 'bad' : '';
      connection.querySelector('.pill-text').textContent = CONNECTION_TEXT[state.connection];
      banner.hidden = state.connection !== 'lost';
      banner.textContent = '與平台連線中斷：畫面上是最後收到的資料，疲勞提醒暫時無法送達。';
      const app = state.app;
      if (!app) return;
      const paused = state.level === 'paused';
      riderName.textContent = app.rider.name;
      updateFatigue(state);

      renderIfChanged(dutyMount, `${app.on_duty}`, () => actionButton(
        { class: `btn btn-xl duty ${app.on_duty ? '' : 'btn-primary'}`, 'aria-pressed': String(app.on_duty) },
        app.on_duty ? '下線休息' : '上線接單', () => actions.onSetDuty(!app.on_duty)));

      const orderKey = app.offer ? `offer:${app.offer.id}`
        : app.order ? `order:${app.order.id}:${app.order.state}:${paused}:${state.navigating}`
          : paused && app.on_duty ? 'rest'
            : `idle:${app.on_duty}:${app.dispatch_blocked}:${app.last_closed ? app.last_closed.id + app.last_closed.state : ''}`;
      renderIfChanged(ordersMount, orderKey, () => {
        if (app.offer) return offerCard(app.offer, orderMap.node, actions);
        // While navigating, the (single) map lives in the navigation screen; it comes back when that closes.
        if (app.order) return activeOrderCard(app.order, paused, state.navigating ? null : orderMap.node, actions);
        if (paused && app.on_duty) return restCard(config);
        if (app.on_duty && app.dispatch_blocked === 'no_signal') return noSignalCard(app.rider);
        return idleCard(app);
      });
      updateRestCard(ordersMount, app.rider, config);
      const reason = ordersMount.querySelector('#no-signal-reason');
      if (reason) reason.textContent = unknownReason(app.rider);
      const countdown = ordersMount.querySelector('#offer-countdown');
      if (countdown && app.offer) countdown.textContent = `${Math.ceil(app.offer.expires_in)} 秒內回覆`;
      const shown = app.offer || app.order;
      fatigueMount.dataset.compact = String(Boolean(shown));  // with an order on screen the map needs the room
      const tripInfo = ordersMount.querySelector('.trip-info');
      if (tripInfo && shown) tripInfo.textContent = tripText(shown, state.route);
      if (!state.navigating) orderMap.update({ order: shown, route: state.route, position: state.position });

      renderIfChanged(statsMount, `${app.stats.delivered}:${app.stats.earnings}`, () => [
        el('div', { class: 'stat' }, el('span', { class: 'stat-value', text: String(app.stats.delivered) }),
          el('span', { class: 'stat-label', text: '今日完成' })),
        el('div', { class: 'stat' }, el('span', { class: 'stat-value', text: fmtMoney(app.stats.earnings) }),
          el('span', { class: 'stat-label', text: '今日收入' })),
      ]);
    },
  };
}
