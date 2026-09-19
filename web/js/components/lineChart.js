// Score-over-time line chart (SVG, no dependencies). One series per chart, so
// no legend — the card title names it. Thresholds are direct-labelled.
//
//   const chart = createLineChart(container, { height, compact, yMax, windowSec, thresholds });
//   chart.update(points /* [[epochSec, value], ...] */, { dim, now });
//
// Missing data is shown as a gap, never interpolated: a link dropout must be
// visible as a hole in the line, not a confident straight segment.

import { fmtClock, fmtScore } from '../utils/format.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const GAP_SEC = 3.5;

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

export function createLineChart(container, options) {
  const opt = { height: 220, compact: false, yMax: 30, windowSec: 300, thresholds: [], ...options };
  const margin = opt.compact
    ? { top: 8, right: 26, bottom: 6, left: 4 }
    : { top: 14, right: 64, bottom: 24, left: 30 };

  container.classList.add('chart');
  const root = svg('svg', { role: 'img', tabindex: '0' });
  const tooltip = document.createElement('div');
  tooltip.className = 'tooltip';
  tooltip.hidden = true;
  container.append(root, tooltip);

  let width = container.clientWidth || 600;
  let last = { points: [], dim: false, now: Date.now() / 1000 };
  let hoverIndex = null;
  let visible = [];
  let scale = null;

  function render() {
    const { points, dim, now } = last;
    const h = opt.height;
    const innerW = Math.max(10, width - margin.left - margin.right);
    const innerH = h - margin.top - margin.bottom;
    const t0 = now - opt.windowSec;
    visible = points.filter((p) => p[0] >= t0);
    const peak = visible.reduce((m, p) => Math.max(m, p[1]), 0);
    const yMax = Math.max(opt.yMax, Math.ceil((peak * 1.1) / 10) * 10);
    const x = (t) => margin.left + ((t - t0) / opt.windowSec) * innerW;
    const y = (v) => margin.top + innerH - (v / yMax) * innerH;
    scale = { x, y, t0, innerW, innerH };

    root.setAttribute('viewBox', `0 0 ${width} ${h}`);
    root.setAttribute('height', h);
    root.replaceChildren();
    container.dataset.dim = String(!!dim);

    // grid + y ticks
    const grid = svg('g', { class: 'grid' });
    const step = yMax <= 30 ? 10 : 20;
    for (let v = step; v <= yMax; v += step) {
      grid.append(svg('line', { x1: margin.left, x2: margin.left + innerW, y1: y(v), y2: y(v) }));
      if (!opt.compact) {
        const label = svg('text', { class: 'tick-label', x: margin.left - 6, y: y(v) + 4, 'text-anchor': 'end' });
        label.textContent = v;
        root.append(label);
      }
    }
    root.append(grid);
    root.append(svg('line', { class: 'baseline', x1: margin.left, x2: margin.left + innerW, y1: y(0), y2: y(0) }));

    // x ticks: minutes before now
    if (!opt.compact) {
      const zero = svg('text', { class: 'tick-label', x: margin.left - 6, y: y(0) + 4, 'text-anchor': 'end' });
      zero.textContent = '0';
      root.append(zero);
      for (let s = 0; s <= opt.windowSec; s += 60) {
        const label = svg('text', { class: 'tick-label', x: x(now - s), y: h - 6,
          'text-anchor': s === 0 ? 'end' : 'middle' });
        label.textContent = s === 0 ? '現在' : `−${s / 60} 分`;
        root.append(label);
      }
    }

    // thresholds, direct-labelled at the right edge
    opt.thresholds.forEach((th) => {
      root.append(svg('line', { class: `threshold ${th.kind}`, x1: margin.left, x2: margin.left + innerW,
        y1: y(th.value), y2: y(th.value) }));
      const label = svg('text', { class: 'threshold-label', x: margin.left + innerW + 6, y: y(th.value) + 4 });
      label.textContent = opt.compact ? th.value : `${th.label} ${th.value}`;
      root.append(label);
    });

    if (!visible.length) {
      const empty = svg('text', { class: 'empty', x: margin.left + innerW / 2, y: margin.top + innerH / 2,
        'text-anchor': 'middle' });
      empty.textContent = '等待資料…';
      root.append(empty);
      root.setAttribute('aria-label', '疲勞分數趨勢：尚無資料');
      return;
    }

    // split into contiguous segments at data gaps
    const segments = [[visible[0]]];
    for (let i = 1; i < visible.length; i++) {
      if (visible[i][0] - visible[i - 1][0] > GAP_SEC) segments.push([]);
      segments[segments.length - 1].push(visible[i]);
    }
    segments.forEach((seg) => {
      const path = seg.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
      if (seg.length > 1) {
        const area = `${path}L${x(seg[seg.length - 1][0]).toFixed(1)},${y(0)}L${x(seg[0][0]).toFixed(1)},${y(0)}Z`;
        root.append(svg('path', { class: 'area', d: area }));
      }
      root.append(svg('path', { class: 'line', d: seg.length > 1 ? path : `${path}l0.01,0` }));
    });

    // end marker + the one direct label this chart gets
    const end = visible[visible.length - 1];
    root.append(svg('circle', { class: 'end-dot', cx: x(end[0]), cy: y(end[1]), r: 4 }));
    if (!opt.compact) {
      const endLabel = svg('text', { class: 'end-label', x: x(end[0]) - 8, y: Math.max(12, y(end[1]) - 9),
        'text-anchor': 'end' });
      endLabel.textContent = fmtScore(end[1]);
      root.append(endLabel);
    }
    root.setAttribute('aria-label', `疲勞分數趨勢，最新 ${fmtScore(end[1])}，${fmtClock(end[0])}`);

    if (hoverIndex != null) drawHover();

    // hit layer last so it sits on top; the whole plot is the target, not the 2px line
    const hit = svg('rect', { x: margin.left, y: margin.top, width: innerW, height: innerH, fill: 'transparent' });
    hit.addEventListener('pointermove', onPointer);
    hit.addEventListener('pointerleave', clearHover);
    root.append(hit);
  }

  function drawHover() {
    const p = visible[Math.min(hoverIndex, visible.length - 1)];
    if (!p) return;
    const { x, y, innerH } = scale;
    root.append(svg('line', { class: 'crosshair', x1: x(p[0]), x2: x(p[0]), y1: margin.top, y2: margin.top + innerH }));
    root.append(svg('circle', { class: 'hover-dot', cx: x(p[0]), cy: y(p[1]), r: 5 }));

    tooltip.replaceChildren();
    const value = document.createElement('div');
    value.className = 'v';
    value.textContent = fmtScore(p[1]);
    const key = document.createElement('div');
    key.className = 'k';
    key.textContent = `疲勞分數 · ${fmtClock(p[0])}`;
    tooltip.append(value, key);
    tooltip.hidden = false;
    const left = x(p[0]) + 12;
    const flip = left + tooltip.offsetWidth > width;
    tooltip.style.left = `${flip ? x(p[0]) - 12 - tooltip.offsetWidth : left}px`;
    tooltip.style.top = `${Math.max(0, y(p[1]) - tooltip.offsetHeight - 8)}px`;
  }

  function onPointer(e) {
    if (!visible.length) return;
    const rect = root.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const t = scale.t0 + ((px - margin.left) / scale.innerW) * opt.windowSec;
    let best = 0;
    for (let i = 1; i < visible.length; i++) {
      if (Math.abs(visible[i][0] - t) < Math.abs(visible[best][0] - t)) best = i;
    }
    if (best !== hoverIndex) { hoverIndex = best; render(); }
  }

  function clearHover() {
    hoverIndex = null;
    tooltip.hidden = true;
    render();
  }

  // keyboard: same readout as hover
  root.addEventListener('focus', () => { if (visible.length) { hoverIndex = visible.length - 1; render(); } });
  root.addEventListener('blur', clearHover);
  root.addEventListener('keydown', (e) => {
    if (hoverIndex == null || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault();
    hoverIndex = Math.max(0, Math.min(visible.length - 1, hoverIndex + (e.key === 'ArrowLeft' ? -1 : 1)));
    render();
  });

  new ResizeObserver((entries) => {
    const w = Math.round(entries[0].contentRect.width);
    if (w && w !== width) { width = w; render(); }
  }).observe(container);

  return {
    setHeight(height) {
      if (height === opt.height) return;
      opt.height = height;
      render();
    },
    update(points, { dim = false, now = Date.now() / 1000 } = {}) {
      last = { points, dim, now };
      render();
    },
  };
}
