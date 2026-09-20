// Full-screen fatigue alert. One huge button, readable at arm's length on a
// handlebar mount; colour is never the only signal (icon + headline + voice).

import { ALERT_TEXT, fillRules } from '../utils/text.js';
import { el, renderIfChanged } from '../utils/dom.js';

const ICON = { warning: '!', paused: 'Ⅱ' };

export function createAlertOverlay(mount, { onAcknowledge }) {
  return {
    update(state) {
      const alert = state.alert;
      mount.hidden = !alert;
      if (!alert) { delete mount.dataset.renderKey; return; }
      mount.dataset.level = alert.level;
      const text = ALERT_TEXT[alert.level];
      const fresh = renderIfChanged(mount, `${alert.level}:${alert.hasOrder}:${alert.isTest}`, () => [
        el('div', { class: 'alert-icon', 'aria-hidden': 'true', text: ICON[alert.level] }),
        el('h1', { class: 'alert-title', id: 'alert-title', text: text.title }),
        el('p', { class: 'alert-body', text: fillRules(alert.hasOrder ? text.bodyWithOrder : text.body, state.config) }),
        alert.isTest && el('p', { class: 'alert-test', text: '這是測試，不會回報給平台' }),
        el('button', { class: 'btn btn-xl alert-button', type: 'button', text: text.button, onclick: onAcknowledge }),
      ]);
      if (fresh) {
        mount.setAttribute('aria-labelledby', 'alert-title');
        mount.querySelector('.alert-button').focus({ preventScroll: true });
      }
    },
  };
}
