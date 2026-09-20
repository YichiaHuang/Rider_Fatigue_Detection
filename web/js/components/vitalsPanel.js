// Heart-rate readout (PPG sensor): rate, signal state, pulse wave.
// A rate is only ever shown when the board marked the signal "good" — anything
// else shows why there is no number instead of a stale or guessed one.
import { el, fmtNum } from '../utils/format.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const W = 300, H = 56;

const QUALITY_TEXT = {
  good: '訊號良好',
  holding: '訊號短暫不穩，沿用剛才的數值',
  weak: '訊號不穩，請保持不動',
  settling: '已接觸，量測中…',
  no_contact: '感測器未接觸皮膚',
};

// PPG drowsiness indicator (rider/ppg_fatigue.py). The verdict is never shown
// without the numbers it was made from: baseline, recent, % change, thresholds.
const FATIGUE_TEXT = {
  no_signal: '心率疲勞指標：訊號不足，暫不判定',
  learning: '心率疲勞指標：建立個人基準中',
  normal: '心率疲勞指標：正常',
  pattern: '心率疲勞指標：出現徵兆（心率降、HRV 升），持續夠久才計分',
  elevated: '心率疲勞指標：徵兆持續，正在加分',
};
const signed = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${fmtNum(v, 0)}%`);

function fatigueDetail(f) {
  if (f.state === 'learning') return `已收集 ${Math.round((f.baseline_progress || 0) * 100)}% 的清醒基準`;
  if (f.baseline_hr_bpm == null) return '';
  // A baseline carried over from before a restart belongs to whoever wore the sensor then — say so.
  const age = f.baseline_age_sec == null ? '' : `，${f.baseline_restored ? '沿用 ' : ''}${Math.round(f.baseline_age_sec / 60)} 分鐘前建立`;
  const parts = [`基準 ${fmtNum(f.baseline_hr_bpm, 0)} bpm／RMSSD ${fmtNum(f.baseline_rmssd_ms, 0)} ms${age}`];
  if (f.hr_change_pct != null) {
    parts.push(`近期心率 ${signed(f.hr_change_pct)}（門檻 −${fmtNum(f.hr_drop_pct, 0)}%）`,
      `RMSSD ${signed(f.rmssd_change_pct)}（門檻 +${fmtNum(f.rmssd_rise_pct, 0)}%）`);
  }
  if (f.state === 'pattern') parts.push(`已持續 ${f.pattern_sec}／${fmtNum(f.sustain_sec, 0)} 秒`);
  if (f.bonus > 0) parts.push(`加分 +${fmtNum(f.bonus, 1)}（上限 ${fmtNum(f.bonus_cap, 0)}）`);
  return parts.join(' · ');
}

export function createVitalsPanel() {
  const value = el('span', { class: 'value', text: '—' });
  const state = el('span', { class: 'vitals-state' });
  const extra = el('div', { class: 'card-sub' });
  const fatigueState = el('div', { class: 'ppg-fatigue-state' });
  const fatigueNumbers = el('div', { class: 'card-sub' });
  const fatigue = el('div', { class: 'ppg-fatigue', hidden: '' }, fatigueState, fatigueNumbers);
  // Raw sensor numbers, always shown while data arrives — this is what you watch
  // while positioning the sensor, long before a heart rate can be trusted.
  const RAW = [
    { key: 'ir_dc', label: '訊號強度 IR', hint: '貼好 > 50,000', fmt: (v) => Math.round(v).toLocaleString('en-US') },
    { key: 'perfusion_index', label: '灌流指數', hint: '% · 越高越好', fmt: (v) => fmtNum(v, 2) },
    { key: 'spectral_peak', label: '頻譜集中度', hint: '≥ 0.55 才採信', fmt: (v) => fmtNum(v, 2) },
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
    wave, extra, fatigue, rawTiles);

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
        fatigue.hidden = true;
        return;
      }
      fatigue.hidden = !vitals.fatigue;
      if (vitals.fatigue) {
        fatigue.dataset.state = vitals.fatigue.state;
        // "no_signal" after the baseline is learned means "nothing current to compare":
        // say so, or it reads as if the baseline were lost.
        fatigueState.textContent = vitals.fatigue.state === 'no_signal' && vitals.fatigue.baseline_hr_bpm != null
          ? '心率疲勞指標：基準已建立，等待目前的心率訊號（戴穩約 1–2 分鐘後判定）'
          : FATIGUE_TEXT[vitals.fatigue.state] || vitals.fatigue.state;
        fatigueNumbers.textContent = fatigueDetail(vitals.fatigue);
      }
      node.dataset.quality = vitals.quality;
      RAW.forEach((r) => { r.v.textContent = vitals[r.key] == null ? '—' : r.fmt(vitals[r.key]); });
      value.textContent = vitals.heart_rate_bpm == null ? '—' : Math.round(vitals.heart_rate_bpm);
      state.textContent = (QUALITY_TEXT[vitals.quality] || vitals.quality)
        + (vitals.quality === 'holding' && vitals.held_sec != null ? `（${Math.round(vitals.held_sec)} 秒前）` : '');
      const parts = [];
      if (vitals.rmssd_ms != null) parts.push(`HRV (RMSSD) ${fmtNum(vitals.rmssd_ms, 0)} ms`);
      else if (vitals.rmssd_pairs != null && vitals.heart_rate_bpm != null) parts.push(`HRV 累積中 ${Math.round(vitals.rmssd_pairs)}／30 組`);
      if (vitals.perfusion_index != null) parts.push(`灌流指數 ${fmtNum(vitals.perfusion_index, 2)}%`);
      extra.textContent = parts.join(' · ') || (vitals.quality === 'no_contact' ? '請將感測器貼緊皮膚' : '');

      const pts = vitals.waveform || [];
      path.setAttribute('d', pts.map((v, i) =>
        `${i ? 'L' : 'M'}${((i / (pts.length - 1)) * W).toFixed(1)},${(H / 2 - v * (H / 2 - 4)).toFixed(1)}`).join(''));
      wave.setAttribute('aria-label', pts.length ? '最近 6 秒的脈搏波形' : '脈搏波形：無訊號');
    },
  };
}
