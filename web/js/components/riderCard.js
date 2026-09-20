// Compact card for one fleet rider: score, status, source tag, small chart.
// Small multiples: every card uses the same scale and thresholds so they compare at a glance.
import { SOURCE_TEXT, el, fmtAge, fmtScore, unknownReason } from '../utils/format.js';
import { createLineChart } from './lineChart.js';
import { createStatusBadge } from './statusBadge.js';

export function createRiderCard(config, { onSelect }) {
  let riderId = null;
  const name = el('h3', { class: 'card-title' });
  const tag = el('span', { class: 'tag' });
  const score = el('span', { class: 'score', text: '—' });
  const badge = createStatusBadge();
  const age = el('span', { class: 'age' });
  const chartBox = el('div');
  const camera = el('span', { class: 'tag', text: '有攝影機', hidden: '' });
  const node = el('article', { class: 'card rider-card', role: 'button', tabindex: '0',
    onclick: () => onSelect(riderId),
    onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(riderId); } } },
    el('div', { class: 'card-head' }, name, tag, camera),
    el('div', { class: 'row' }, score, badge.node, age),
    chartBox);

  const chart = createLineChart(chartBox, {
    height: 76, compact: true, yMax: config.score_display_max, windowSec: 300,
    thresholds: [
      { value: config.pause_threshold, kind: 'pause', label: '暫停' },
      { value: config.resume_threshold, kind: 'resume', label: '恢復' },
    ],
  });

  return {
    node,
    update(rider) {
      const unknown = rider.status === 'unknown';
      riderId = rider.id;
      node.dataset.status = rider.status;
      node.setAttribute('aria-label', `${rider.name}，點選放大檢視`);
      camera.hidden = !rider.has_board;
      name.textContent = rider.name;
      tag.textContent = SOURCE_TEXT[rider.source] || rider.source;
      tag.dataset.source = rider.source;
      score.textContent = !unknown && rider.link === 'online' ? fmtScore(rider.score) : '—';
      badge.update(rider.status);
      age.textContent = unknown ? unknownReason(rider).split('，')[0] : fmtAge(rider.age_sec);
      chart.update(rider.history, { dim: unknown });
    },
  };
}
