// Status is never colour-only: icon + label + colour, same wording everywhere.
import { STATUS_TEXT, el } from '../utils/format.js';

export function createStatusBadge({ large = false } = {}) {
  const icon = el('span', { class: 'icon', 'aria-hidden': 'true' });
  const label = el('span');
  const node = el('span', { class: `status${large ? ' large' : ''}`, role: 'status' }, icon, label);
  return {
    node,
    update(status) {
      const text = STATUS_TEXT[status] || STATUS_TEXT.unknown;
      node.dataset.status = status;
      icon.textContent = text.icon;
      label.textContent = text.label;
    },
  };
}
