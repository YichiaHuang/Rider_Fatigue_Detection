// First screen: who is riding, and the one tap that browsers require before an
// app may make sound, speak or ask for notification permission.

import { settings } from '../config.js';
import { el } from '../utils/dom.js';

const SOURCE_TEXT = { real: '真實偵測裝置', simulated: '模擬資料（展示用）' };

export function createLogin(mount, { loadRiders, channelSupport, onStart }) {
  let riders = [];
  let selectedId = settings.riderId;
  let error = null;

  const list = el('div', { class: 'rider-list', role: 'radiogroup', 'aria-label': '選擇騎手' });
  const startButton = el('button', { class: 'btn btn-primary btn-xl', type: 'button', text: '開始使用', onclick: start });
  const message = el('p', { class: 'login-message', role: 'status' });
  const serverInput = el('input', {
    class: 'input', type: 'url', inputmode: 'url', placeholder: '留空＝目前這個網址', value: settings.apiBase,
    'aria-label': '平台伺服器位址',
  });

  mount.append(
    el('div', { class: 'login' },
      el('div', { class: 'login-brand' },
        el('img', { src: 'icons/icon.svg', alt: '', width: 64, height: 64 }),
        el('h1', { text: '騎手夥伴' }),
        el('p', { class: 'muted', text: '外送接單＋疲勞提醒' })),
      el('h2', { class: 'section-title', text: '你是哪一位騎手？' }),
      list,
      el('h2', { class: 'section-title', text: '這支手機可用的功能' }),
      el('ul', { class: 'support-list' }, channelSupport().map((c) => el('li', { data: { ok: String(c.ok) } },
        el('span', { class: 'support-icon', 'aria-hidden': 'true', text: c.ok ? '✓' : '—' }),
        el('span', {}, el('strong', { text: c.name }), el('span', { class: 'muted', text: `　${c.note}` }))))),
      message,
      startButton,
      el('p', { class: 'fineprint', text: '按下後會請你允許通知，並播放一次提示音確認手機音量。騎乘中請勿操作手機。' }),
      el('details', { class: 'advanced' },
        el('summary', { text: '進階：平台伺服器位址' }),
        serverInput,
        el('button', { class: 'btn', type: 'button', text: '套用並重新載入', onclick: () => {
          settings.apiBase = serverInput.value.trim();
          refresh();
        } }))));

  function render() {
    list.replaceChildren(...riders.map((r) => el('button', {
      class: 'rider-option', type: 'button', role: 'radio', 'aria-checked': String(r.id === selectedId),
      onclick: () => { selectedId = r.id; render(); },
    },
    el('span', { class: 'rider-name', text: r.name }),
    el('span', { class: 'tag', data: { source: r.source }, text: SOURCE_TEXT[r.source] || r.source }))));
    startButton.disabled = !riders.some((r) => r.id === selectedId);
    message.textContent = error || (riders.length ? '' : '讀取騎手名單中…');
    message.dataset.state = error ? 'error' : '';
  }

  async function refresh() {
    error = null;
    try {
      riders = (await loadRiders()).riders;
    } catch (e) {
      riders = [];
      error = `連不上平台伺服器（${e.message}）。確認網址與網路後再試一次。`;
    }
    render();
  }

  function start() {
    if (!riders.some((r) => r.id === selectedId)) return;
    onStart(selectedId);  // still inside the tap: main.js unlocks audio / speech / notifications here
  }

  return {
    show() { mount.hidden = false; selectedId = settings.riderId || selectedId; refresh(); },
    hide() { mount.hidden = true; },
  };
}
