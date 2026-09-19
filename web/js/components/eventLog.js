// Dispatch / link event log, newest first. Doubles as the accessible record of
// every pause and resume shown on the charts.
import { EVENT_TEXT, el, fmtClock, fmtScore } from '../utils/format.js';

export function createEventLog(mount) {
  const body = el('tbody');
  let renderedTop = null;
  mount.append(
    el('div', { class: 'card-head' },
      el('h2', { class: 'card-title', text: '派單事件紀錄' }),
      el('span', { class: 'card-sub', text: '暫停、恢復與訊號狀態變化' })),
    el('div', { class: 'table-wrap' },
      el('table', { class: 'data' },
        el('thead', {}, el('tr', {},
          el('th', { text: '時間' }), el('th', { text: '騎手' }), el('th', { text: '事件' }), el('th', { text: '當時分數' }))),
        body)));

  return {
    update(events) {
      const top = events.length ? events[0].seq : 0;
      if (top === renderedTop) return;
      renderedTop = top;
      body.replaceChildren(...(events.length
        ? events.slice(0, 50).map((e) => el('tr', {},
            el('td', { class: 'num', text: fmtClock(e.time) }),
            el('td', { text: e.rider_name }),
            el('td', { text: EVENT_TEXT[e.kind] || e.kind }),
            el('td', { class: 'num', text: fmtScore(e.score) })))
        : [el('tr', {}, el('td', { class: 'empty', colspan: '4', text: '尚無事件' }))]));
    },
  };
}
