// Heart-rate readout (PPG sensor): rate, signal state, pulse wave.
// A rate is only ever shown when the board marked the signal "good" — anything
// else shows why there is no number instead of a stale or guessed one.
import { el, fmtNum } from '../utils/format.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const W = 300, H = 56;

const QUALITY_TEXT = {
  good: '訊號良好',
  weak: '訊號不穩，請保持不動',
  settling: '已接觸，量測中…',
  no_contact: '感測器未接觸皮膚',
};

export function createVitalsPanel() {
  const value = el('span', { class: 'value', text: '—' });
  const state = el('span', { class: 'vitals-state' });
  const extra = el('div', { class: 'card-sub' });
  // Raw sensor numbers, always shown while data arrives — this is what you watch
  // while positioning the sensor, long before a heart rate can be trusted.
  const RAW = [
    { key: 'ir_dc', label: '訊號強度 IR', hint: '貼好 > 50,000', fmt: (v) => Math.round(v).toLocaleString('en-US') },
    { key: 'perfusion_index', label: '灌流指數', hint: '% · 越高越好', fmt: (v) => fmtNum(v, 2) },
    { key: 'autocorr', label: '週期性', hint: '≥ 0.30 才採信', fmt: (v) => fmtNum(v, 2) },
    { key: 'sample_hz', label: '取樣率', hint: 'Hz · 應為 50', fmt: (v) => fmtNum(v, 1) },
  ].map((r) => {
    const v = el('div', { class: 'value', text: '—' });
    return { ...r, v, node: el('div', { class: 'tile' }, el('div', { class: 'label', text: r.label }), v,
      el('div', { class: 'hint', text: r.hint })) };
  });
  const rawTiles = el('div', { class: 'tiles vitals-raw' }, RAW.map((r) => r.node));
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('class', 'pulse-line');
  const wave = document.createElementNS(SVG_NS, 'svg');
  wave.setAttribute('viewBox', `0 0 ${W} ${H}`);
  wave.setAttribute('preserveAspectRatio', 'none');
  wave.setAttribute('class', 'pulse-wave');
  wave.setAttribute('role', 'img');
  wave.append(path);

  const node = el('div', { class: 'vitals' },
    el('div', { class: 'card-sub', text: '心率（PPG 感測器）' }),
    el('div', { class: 'vitals-row' },
      el('div', { class: 'vitals-rate' }, value, el('span', { class: 'unit', text: 'bpm' })), state),
    wave, extra, rawTiles);

  return {
    node,
    update(vitals) {
      if (!vitals) {
        node.dataset.quality = 'none';
        value.textContent = '—';
        state.textContent = '沒有心率資料';
        extra.textContent = '僅在展示模式傳送；一般模式平台不會收到生理數據';
        path.setAttribute('d', '');
        wave.setAttribute('aria-label', '脈搏波形：無資料');
        RAW.forEach((r) => { r.v.textContent = '—'; });
        return;
      }
      node.dataset.quality = vitals.quality;
      RAW.forEach((r) => { r.v.textContent = vitals[r.key] == null ? '—' : r.fmt(vitals[r.key]); });
      value.textContent = vitals.heart_rate_bpm == null ? '—' : Math.round(vitals.heart_rate_bpm);
      state.textContent = QUALITY_TEXT[vitals.quality] || vitals.quality;
      const parts = [];
      if (vitals.rmssd_ms != null) parts.push(`HRV (RMSSD) ${fmtNum(vitals.rmssd_ms, 0)} ms`);
      if (vitals.perfusion_index != null) parts.push(`灌流指數 ${fmtNum(vitals.perfusion_index, 2)}%`);
      extra.textContent = parts.join(' · ') || (vitals.quality === 'no_contact' ? '請將感測器貼緊皮膚' : '');

      const pts = vitals.waveform || [];
      path.setAttribute('d', pts.map((v, i) =>
        `${i ? 'L' : 'M'}${((i / (pts.length - 1)) * W).toFixed(1)},${(H / 2 - v * (H / 2 - 4)).toFixed(1)}`).join(''));
      wave.setAttribute('aria-label', pts.length ? '最近 6 秒的脈搏波形' : '脈搏波形：無訊號');
    },
  };
}
