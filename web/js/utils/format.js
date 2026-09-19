// Display strings in one place so wording stays consistent across components.

export const STATUS_TEXT = {
  normal: { label: '正常派單', icon: '✓' },
  paused: { label: '暫停新單', icon: 'Ⅱ' },
  unknown: { label: '未知／訊號中斷', icon: '?' },
};

export const SOURCE_TEXT = { real: '真實裝置', simulated: '模擬資料', replay: '錄影回放' };

export const REASON_TEXT = {
  perclos: '閉眼比例偏高',
  yawn: '打哈欠',
  head_down: '持續低頭',
  audio: '音訊異常',
};

export const EVENT_TEXT = {
  paused: '暫停新單',
  resumed: '恢復派單',
  link_stale: '訊號延遲',
  link_offline: '訊號中斷',
  link_restored: '訊號恢復',
  registered: '新裝置上線',
  perception_no_face: '偵測不到人臉',
  perception_camera_error: '攝影機故障',
};

export function unknownReason(rider) {
  if (rider.perception === 'no_face') return '偵測不到人臉，分數暫停更新';
  if (rider.perception === 'camera_error') return '攝影機故障';
  if (rider.link === 'waiting') return '尚未收到資料';
  if (rider.link === 'stale') return '訊號延遲，以下為最後一筆分數';
  if (rider.link === 'offline') return '訊號中斷，以下為最後一筆分數';
  return '';
}

export const fmtScore = (v) => (v == null ? '—' : v.toFixed(1));
export const fmtNum = (v, digits = 2) => (v == null ? '—' : v.toFixed(digits));

export function fmtClock(epochSec) {
  return new Date(epochSec * 1000).toLocaleTimeString('zh-TW', { hour12: false });
}

export function fmtAge(sec) {
  if (sec == null) return '';
  if (sec < 1.5) return '剛剛';
  if (sec < 90) return `${Math.round(sec)} 秒前`;
  return `${Math.round(sec / 60)} 分鐘前`;
}

// Tiny DOM helper: el('div', {class:'x', text:'y'}, child, child)
// Text always goes through textContent — rider names and reasons are untrusted input.
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'data') Object.assign(node.dataset, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  children.flat().forEach((c) => c != null && node.append(c));
  return node;
}
