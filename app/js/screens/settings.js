// Settings sheet: which alert channels are on, a rehearsal button for each
// alert, and signing out. Channels this phone can't do are shown as such
// instead of as a switch that silently does nothing.

import { settings } from '../config.js';
import { el } from '../utils/dom.js';

const CHANNELS = [
  { key: 'sound', name: '提示音' },
  { key: 'voice', name: '語音播報' },
  { key: 'vibration', name: '震動' },
  { key: 'notification', name: '系統通知' },
];

export function createSettings(mount, { channelSupport, onTest, onEnableNotifications, onSignOut, onDemoRideChange }) {
  function close() { mount.hidden = true; }

  function render() {
    const support = Object.fromEntries(channelSupport().map((c) => [c.key, c]));
    const channels = settings.channels;
    const rows = CHANNELS.map(({ key, name }) => {
      const info = support[key];
      const input = el('input', {
        type: 'checkbox', role: 'switch', checked: channels[key] && info.ok, disabled: !info.ok,
        onchange: async (e) => {
          if (key === 'notification' && e.target.checked) await onEnableNotifications();
          settings.channels = { ...settings.channels, [key]: e.target.checked };
          render();
        },
      });
      return el('label', { class: 'setting-row' },
        el('span', {}, el('strong', { text: name }), el('span', { class: 'setting-note', text: info.note })),
        input);
    });

    mount.replaceChildren(el('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': '設定' },
      el('div', { class: 'sheet-head' },
        el('h2', { text: '提醒設定' }),
        el('button', { class: 'icon-button', type: 'button', 'aria-label': '關閉', text: '✕', onclick: close })),
      rows,
      el('h3', { class: 'section-title', text: '試聽提醒（停車時再試）' }),
      el('div', { class: 'button-row' },
        el('button', { class: 'btn grow', type: 'button', text: '注意疲勞', onclick: () => { close(); onTest('warning'); } }),
        el('button', { class: 'btn grow', type: 'button', text: '暫停派單', onclick: () => { close(); onTest('paused'); } })),
      el('h3', { class: 'section-title', text: '展示用' }),
      el('label', { class: 'setting-row' },
        el('span', {}, el('strong', { text: '模擬騎乘' }),
          el('span', { class: 'setting-note', text: '不讀手機定位；開始導航後，位置會自己沿著路線前進。畫面會標示「模擬定位」' })),
        el('input', { type: 'checkbox', role: 'switch', checked: settings.demoRide,
          onchange: (e) => { settings.demoRide = e.target.checked; onDemoRideChange(); render(); } })),
      el('button', { class: 'btn btn-quiet', type: 'button', text: '登出／切換騎手', onclick: () => { close(); onSignOut(); } })));
  }

  mount.addEventListener('click', (e) => { if (e.target === mount) close(); });

  return { open() { render(); mount.hidden = false; } };
}
