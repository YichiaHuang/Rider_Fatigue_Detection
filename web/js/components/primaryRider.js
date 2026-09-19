// The large readout for the primary (real) rider: hero score, dispatch status,
// demo-mode feature tiles, which rules fired, and the score chart + table view.
import { REASON_TEXT, SOURCE_TEXT, el, fmtAge, fmtClock, fmtNum, fmtScore, unknownReason } from '../utils/format.js';
import { createLineChart } from './lineChart.js';
import { createStatusBadge } from './statusBadge.js';

const TILES = [
  { key: 'ear', label: 'EAR 眼睛開合', digits: 2, hint: '越小越閉' },
  { key: 'mar', label: 'MAR 嘴巴開合', digits: 2, hint: '打哈欠時升高' },
  { key: 'perclos', label: 'PERCLOS 閉眼比例', digits: 2, hint: '滑動視窗' },
  { key: 'head_pitch_deg', label: '頭部俯角', digits: 1, hint: '度，正值＝低頭' },
];

export function createPrimaryRider(readoutMount, chartMount, config) {
  let lastHistory = [];

  // ---- readout ----
  const name = el('h2', { class: 'card-title' });
  const tag = el('span', { class: 'tag' });
  const value = el('span', { class: 'value', text: '—' });
  const hero = el('div', { class: 'hero-score' }, value, el('span', { class: 'unit', text: '疲勞分數' }));
  const badge = createStatusBadge({ large: true });
  const note = el('div', { class: 'readout-note' });
  const tileNodes = TILES.map((t) => {
    const v = el('div', { class: 'value', text: '—' });
    return { ...t, v, node: el('div', { class: 'tile' },
      el('div', { class: 'label', text: t.label }), v, el('div', { class: 'hint', text: t.hint })) };
  });
  const reasons = el('div', { class: 'reasons' });
  const detailNote = el('div', { class: 'card-sub' });

  readoutMount.classList.add('readout');
  readoutMount.append(
    el('div', { class: 'card-head' }, name, tag),
    hero, el('div', {}, badge.node), note,
    el('div', { class: 'tiles' }, tileNodes.map((t) => t.node)),
    el('div', {}, el('div', { class: 'card-sub', text: '本次加分原因' }), reasons),
    detailNote);

  // ---- chart + table twin ----
  const chartBox = el('div');
  const tableBody = el('tbody');
  const tableWrap = el('div', { class: 'table-wrap', hidden: '' },
    el('table', { class: 'data' },
      el('thead', {}, el('tr', {}, el('th', { text: '時間' }), el('th', { text: '疲勞分數' }))), tableBody));
  const toggle = el('button', { class: 'btn', type: 'button', text: '顯示數據表', 'aria-pressed': 'false',
    onclick: () => {
      const showTable = tableWrap.hidden;
      tableWrap.hidden = !showTable;
      chartBox.hidden = showTable;
      toggle.textContent = showTable ? '顯示圖表' : '顯示數據表';
      toggle.setAttribute('aria-pressed', String(showTable));
      if (showTable) renderTable(lastHistory);
    } });
  chartMount.append(
    el('div', { class: 'card-head' },
      el('h2', { class: 'card-title', text: '疲勞分數趨勢' }),
      el('span', { class: 'card-sub', text: `最近 5 分鐘 · 達 ${config.pause_threshold} 暫停新單，降到 ${config.resume_threshold} 恢復` }),
      el('span', { class: 'spacer' }), toggle),
    chartBox, tableWrap);

  const chart = createLineChart(chartBox, {
    height: 240, yMax: config.score_display_max, windowSec: 300,
    thresholds: [
      { value: config.pause_threshold, kind: 'pause', label: '暫停' },
      { value: config.resume_threshold, kind: 'resume', label: '恢復' },
    ],
  });

  return {
    setCompact(compact) { chart.setHeight(compact ? 150 : 240); },
    update(rider) {
      const unknown = rider.status === 'unknown';
      name.textContent = rider.name;
      tag.textContent = SOURCE_TEXT[rider.source] || rider.source;
      tag.dataset.source = rider.source;
      value.textContent = fmtScore(rider.score);
      hero.dataset.dim = String(unknown);
      badge.update(rider.status);
      note.textContent = unknown
        ? unknownReason(rider)
        : `更新於 ${fmtAge(rider.age_sec)}${rider.dispatch === 'paused' ? ` · 需降到 ${config.resume_threshold} 以下才恢復` : ''}`;

      const d = rider.detail;
      tileNodes.forEach((t) => { t.v.textContent = d ? fmtNum(d[t.key], t.digits) : '—'; });
      reasons.replaceChildren(...(d && d.reasons.length
        ? d.reasons.map((r) => el('span', { class: 'reason', text: REASON_TEXT[r] || r }))
        : [el('span', { class: 'none', text: d ? '目前沒有規則觸發' : '—' })]));
      detailNote.textContent = d
        ? `特徵細節僅在展示模式傳送${d.inference_fps != null ? ` · 推論 ${fmtNum(d.inference_fps, 1)} FPS` : ''}`
        : '一般模式不傳送特徵細節，平台只有分數';

      chart.update(rider.history, { dim: unknown });
      if (!tableWrap.hidden) renderTable(rider.history);
      lastHistory = rider.history;
    },
  };

  function renderTable(history) {
    tableBody.replaceChildren(...history.slice(-60).reverse().map((p) =>
      el('tr', {}, el('td', { class: 'num', text: fmtClock(p[0]) }), el('td', { class: 'num', text: fmtScore(p[1]) }))));
  }
}
