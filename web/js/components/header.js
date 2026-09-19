// Top bar: title, data-link state, board mode, clock, theme toggle.
import { el, fmtClock } from '../utils/format.js';

const CONNECTION = {
  connecting: { text: '連線中…', state: '' },
  live: { text: '平台資料即時更新', state: 'ok' },
  lost: { text: '與平台伺服器中斷，重新連線中', state: 'bad' },
};

export function createHeader(mount) {
  const connDot = el('span', { class: 'dot' });
  const connText = el('span');
  const conn = el('span', { class: 'pill' }, connDot, connText);

  const modeDot = el('span', { class: 'dot' });
  const modeText = el('span');
  const mode = el('span', { class: 'pill' }, modeDot, modeText);

  const clock = el('span', { class: 'clock' });
  const theme = el('button', { class: 'btn', type: 'button', text: '切換深／淺色',
    onclick: () => {
      const root = document.documentElement;
      const dark = root.dataset.theme
        ? root.dataset.theme === 'dark'
        : window.matchMedia('(prefers-color-scheme: dark)').matches;
      root.dataset.theme = dark ? 'light' : 'dark';
      try { localStorage.setItem('theme', root.dataset.theme); } catch (e) { /* private mode */ }
    } });

  mount.append(
    el('div', {},
      el('h1', { class: 'brand', text: '外送騎手疲勞風險監控' }),
      el('div', { class: 'brand-sub', text: '派單平台端 · 疲勞分數達門檻即暫停新單' })),
    el('span', { class: 'spacer' }), conn, mode, clock, theme);

  setInterval(() => { clock.textContent = fmtClock(Date.now() / 1000); }, 1000);
  clock.textContent = fmtClock(Date.now() / 1000);

  return {
    update(state) {
      // "Live" must mean the BOARD's data is arriving, not merely that this page
      // can reach the laptop backend: a dead MQTT channel is reported here.
      const mqtt = (state.sources || []).find((s) => s.name === 'mqtt');
      const c = CONNECTION[state.connection] || CONNECTION.connecting;
      if (state.connection === 'live' && mqtt && !mqtt.connected) {
        connText.textContent = '板子資料通道中斷（MQTT 未連線），畫面為舊資料';
        conn.dataset.state = 'bad';
      } else {
        connText.textContent = c.text;
        conn.dataset.state = c.state;
      }

      const board = state.board;
      if (board.configured === false) {
        modeText.textContent = '此騎手沒有影像裝置';
        mode.dataset.state = '';
      } else if (!board.reachable) {
        modeText.textContent = board.pending ? '連線中…' : '裝置未連線';
        mode.dataset.state = '';
      } else if (board.mode === 'demo') {
        modeText.textContent = '展示串流中 · 影像離開裝置';
        mode.dataset.state = 'live';
      } else {
        modeText.textContent = '一般模式 · 只傳分數';
        mode.dataset.state = 'ok';
      }
    },
  };
}
