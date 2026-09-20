// Tiny DOM helper: el('div', {class:'x', text:'y'}, child, child)
// Text always goes through textContent — shop names and addresses are untrusted input.
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'data') Object.assign(node.dataset, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  children.flat().forEach((c) => c != null && c !== false && node.append(c));
  return node;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  children.flat().forEach((c) => c != null && node.append(c));
  return node;
}

// Re-render a mount point only when what it shows has changed: the state is
// polled every second, and rebuilding a button mid-tap would swallow the tap.
export function renderIfChanged(mount, key, build) {
  if (mount.dataset.renderKey === key) return false;
  mount.dataset.renderKey = key;
  mount.replaceChildren(...[build()].flat().filter(Boolean));
  return true;
}
