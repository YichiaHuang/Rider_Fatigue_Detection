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

  // Stall watchdog. An MJPEG <img> never reconnects by itself: when the board
  // runner restarts or the link blips, the stream ends and the last frame just
  // sits there looking like a switched-off camera. A live camera picture is
  // never pixel-identical twice (sensor noise), so: sample the image into a tiny
  // canvas every 2 s, and if it hasn't changed for 3 checks, reconnect.
  const probe = document.createElement('canvas');
  probe.width = 16; probe.height = 12;
  const probeCtx = probe.getContext('2d', { willReadFrequently: true });
  let lastSignature = null, unchanged = 0, currentRider = null;
  const stallNote = el('span', { text: '' });

  function signature(img) {
    try {
      probeCtx.drawImage(img, 0, 0, probe.width, probe.height);
      const data = probeCtx.getImageData(0, 0, probe.width, probe.height).data;
      let sum = 0;
      for (let i = 0; i < data.length; i += 4) sum = (sum * 31 + data[i] + data[i + 1] * 3 + data[i + 2] * 7) >>> 0;
      return sum;
    } catch (e) { return null; }   // image not decodable yet
  }

  setInterval(() => {
    const img = frame.querySelector('img');
    if (!img || !currentRider) { lastSignature = null; unchanged = 0; return; }
    const sig = img.naturalWidth ? signature(img) : null;
    unchanged = (sig !== null && sig !== lastSignature) ? 0 : unchanged + 1;
    lastSignature = sig;
    if (unchanged >= 3) {           // ~6 s frozen or never loaded: reconnect the stream
      unchanged = 0;
      stallNote.textContent = '影像中斷，重新連線中…';
      img.src = streamUrl(currentRider.id);
    } else if (unchanged === 0) {
      stallNote.textContent = '';
    }
  }, 2000);

  function placeholder(title, body) {
    frame.replaceChildren(el('div', { class: 'video-placeholder' }, el('strong', { text: title }), body));
  }

  return {
    setFocus(focused) {
      focusButton.textContent = focused ? '還原版面' : '放大畫面';
      focusButton.setAttribute('aria-pressed', String(focused));
    },
    update(board, rider) {
      currentRider = rider;
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
        stallNote,
      ] : board.configured === false || board.pending ? []
        : [el('span', { text: '板子未開機或不在同一網路；連上後會自動恢復', title: board.error || '' })]));
    },
  };
}
