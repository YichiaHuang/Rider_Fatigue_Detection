// Live camera panel (plan.md 4.5). Shows the MJPEG stream only while the board
// reports demo mode; otherwise says plainly why there is no picture.
// onToggleMode(demo:boolean) is injected — this component does no networking.
import { el } from '../utils/format.js';

export function createVideoPanel(mount, { streamUrl, onToggleMode, onToggleFocus }) {
  const frame = el('div', { class: 'video-frame' });
  const meta = el('div', { class: 'video-meta' });
  const button = el('button', { class: 'btn', type: 'button' });
  const focusButton = el('button', { class: 'btn', type: 'button', 'aria-pressed': 'false',
    title: '放大攝影機畫面，其餘面板縮小（Esc 還原）', onclick: () => onToggleFocus() });
  let shown = null;  // '<riderId>:stream' | ':normal' | ':offline' | ':none'
  let demo = false;
  let busy = false;

  button.addEventListener('click', async () => {
    busy = true; button.disabled = true;
    try { await onToggleMode(!demo); } catch (e) { alert(`切換失敗：${e.message}`); }
    busy = false; button.disabled = false;
  });

  mount.append(
    el('div', { class: 'card-head' },
      el('h2', { class: 'card-title', text: '即時攝影機畫面' }),
      el('span', { class: 'spacer' }), focusButton, button),
    frame, meta);

  function placeholder(title, body) {
    frame.replaceChildren(el('div', { class: 'video-placeholder' }, el('strong', { text: title }), body));
  }

  return {
    setFocus(focused) {
      focusButton.textContent = focused ? '還原版面' : '放大畫面';
      focusButton.setAttribute('aria-pressed', String(focused));
    },
    update(board, rider) {
      demo = board.reachable && board.mode === 'demo';
      const kind = board.configured === false ? 'none' : !board.reachable ? 'offline' : demo ? 'stream' : 'normal';
      const next = `${rider.id}:${kind}`;  // keyed by rider so switching riders always reloads the picture
      button.textContent = demo ? '關閉展示模式' : '開啟展示模式';
      button.disabled = busy || !board.reachable;

      if (next !== shown) {
        shown = next;
        if (kind === 'stream') {
          const img = el('img', { alt: `${rider.name} 即時攝影機畫面`, src: streamUrl(rider.id) });
          img.addEventListener('error', () => { shown = null; });  // retry on next status poll
          frame.replaceChildren(img);
        } else if (kind === 'normal') {
          placeholder('一般模式', '影像留在裝置上推論，平台只收到分數');
        } else if (kind === 'none') {
          placeholder('沒有攝影機畫面', rider.source === 'simulated' ? '這位是模擬騎手' : '這塊板子沒有設定影像位址（--board）');
        } else {
          placeholder(board.pending ? '連線中…' : '裝置未連線', board.pending ? '' : '連不上板子的影像服務');
        }
      }

      const cam = board.camera || {};
      meta.replaceChildren(...(board.reachable ? [
        el('span', { text: `攝影機 ${cam.ok ? '正常' : '無畫面'}` }),
        el('span', { text: `擷取 ${cam.capture_fps ?? '—'} FPS` }),
        el('span', { text: `串流 ${board.stream_fps ?? '—'} FPS` }),
        el('span', { text: `觀看 ${board.viewers ?? 0}` }),
        el('span', { text: '不自動錄影' }),
      ] : board.configured === false || board.pending ? []
        : [el('span', { text: '板子未開機或不在同一網路；連上後會自動恢復', title: board.error || '' })]));
    },
  };
}
