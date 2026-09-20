// Every string the rider sees or hears, in one place, so wording stays
// consistent between the screen, the voice and the system notification.

export const LEVEL_TEXT = {
  normal: { label: '狀態良好', icon: '✓', hint: '正常接單中' },
  warning: { label: '注意疲勞', icon: '!', hint: '疲勞分數偏高，再升高會暫停派新單' },
  paused: { label: '已暫停派單', icon: 'Ⅱ', hint: '請休息；休息夠久、分數也降下來後自動恢復' },
  unknown: { label: '偵測未連線', icon: '?', hint: '' },
};

export const ALERT_TEXT = {
  warning: {
    title: '注意：疲勞分數升高',
    body: '你可能開始累了。下一個紅燈伸展一下，這一單送完考慮休息。',
    voice: '注意，疲勞分數升高，請留意精神狀況。',
    button: '我知道了',
  },
  paused: {
    title: '偵測到疲勞，已暫停派新單',
    body: '請找安全的地方停車休息。至少休息 {rest}，而且疲勞分數降到 {resume} 以下，才會恢復派單。',
    bodyWithOrder: '請先專心、慢慢把手上這一單送完，之後找安全的地方休息。至少休息 {rest}、分數降到 {resume} 以下才會再派新單。',
    voice: '偵測到疲勞，已經暫停派新單。請找安全的地方停車休息。',
    voiceWithOrder: '偵測到疲勞，已經暫停派新單。請慢慢把這一單送完，然後休息。',
    button: '我知道了，我會休息',
  },
  resumed: { title: '已恢復派單', body: '休息得不錯，繼續加油，路上小心。', voice: '已恢復派單，路上小心。' },
  offer: { title: '新訂單' },
  signalLost: { title: '疲勞偵測訊號中斷', voice: '疲勞偵測訊號中斷。' },
};

// "{rest}" / "{resume}" in alert texts -> "1 分鐘" / "8", from the platform's config.
export function fmtDuration(sec) {
  return sec >= 60 && sec % 60 === 0 ? `${sec / 60} 分鐘` : `${Math.round(sec)} 秒`;
}
export function fillRules(text, config) {
  return text.replace('{rest}', fmtDuration(config.min_rest_sec)).replace('{resume}', String(config.resume_threshold));
}
export function fmtCountdown(sec) {
  const whole = Math.ceil(sec);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

export const NO_SIGNAL_TEXT = {
  title: '偵測未連線，暫不派單',
  body: '平台要確認得到你的精神狀況才會派單。偵測裝置連上、鏡頭抓得到臉之後，會自動開始派單。',
  offDuty: '目前偵測未連線：上線後也要等偵測恢復才會派單。',
};

export const REST_TIPS = ['下車走動、伸展 5 分鐘', '喝水，或補充一點含糖飲料', '閉眼休息 10–15 分鐘效果最好'];

export const REASON_TEXT = { perclos: '閉眼時間偏長', yawn: '打哈欠', head_down: '持續低頭', audio: '音訊異常', ppg: '心率出現疲勞徵兆' };

export const ORDER_STEP_TEXT = {
  accepted: { status: '前往取餐', action: '已取餐' },
  picked_up: { status: '外送中', action: '已送達' },
};

export const CLOSED_TEXT = {
  expired: '上一張訂單逾時未回應',
  withdrawn: '上一張訂單已由平台撤回',
  declined: null,
};

export function unknownReason(rider) {
  if (rider.perception === 'no_face') return '鏡頭偵測不到臉，請調整角度';
  if (rider.perception === 'camera_error') return '偵測裝置的攝影機故障';
  if (rider.link === 'waiting') return '尚未收到偵測裝置的資料';
  if (rider.link === 'stale') return '偵測裝置訊號延遲';
  if (rider.link === 'offline') return '偵測裝置離線';
  return '';
}

export const fmtScore = (v) => (v == null ? '—' : v.toFixed(1));
export const fmtMoney = (v) => `$${v}`;

const LEG_TEXT = { pickup: '到取餐點', dropoff: '到送達點' };
const fmtKm = (m) => `${(m / 1000).toFixed(1)} 公里`;
const fmtMin = (s) => `約 ${Math.max(1, Math.round(s / 60))} 分`;

// "到取餐點 1.2 公里・約 4 分　到送達點 2.4 公里・約 8 分"; before a route arrives, the quoted distance.
export function tripText(order, route) {
  if (!route) return `${order.distance_km} 公里`;
  const legs = route.legs.map((leg) => `${LEG_TEXT[leg.to] || ''} ${fmtKm(leg.distance_m)}・${fmtMin(leg.duration_s)}`);
  return legs.join('　') + (route.source === 'estimate' ? '（直線估計）' : '');
}

// ---- in-app navigation (nav/guidance.js speaks these, screens/navigation.js shows them) ----
export const NAV_TEXT = {
  started: '開始導航。', rerouted: '已重新規劃路線。', waitingForFix: '等待定位中…',
  simulated: '模擬定位', estimate: '路線服務連不上：只有直線方向，沒有轉彎指示',
  stops: { pickup: '取餐點', dropoff: '送達點' },
};

const TURN_TEXT = {
  left: '左轉', right: '右轉', 'slight left': '靠左行駛', 'slight right': '靠右行駛',
  'sharp left': '向左急轉', 'sharp right': '向右急轉', uturn: '迴轉', straight: '直行',
};
const TURN_ICON = {
  left: '←', right: '→', 'slight left': '↖', 'slight right': '↗',
  'sharp left': '↙', 'sharp right': '↘', uturn: '↶', straight: '↑',
};

// "右轉 進入 光復路二段" / "進入圓環，第 2 個出口" / "抵達取餐點"
export function maneuverText(step, stopName) {
  if (step.type === 'arrive') return `抵達${stopName || '目的地'}`;
  if (step.type === 'roundabout' || step.type === 'rotary') return `進入圓環${step.exit ? `，第 ${step.exit} 個出口` : ''}`;
  const turn = TURN_TEXT[step.modifier] || '直行';
  if (step.type === 'new name' || (step.type === 'continue' && step.modifier === 'straight')) {
    return step.name ? `直行 進入 ${step.name}` : '直行';
  }
  return step.name ? `${turn} 進入 ${step.name}` : turn;
}

export function maneuverIcon(step) {
  if (step.type === 'arrive') return '◎';
  if (step.type === 'roundabout' || step.type === 'rotary') return '↻';
  return TURN_ICON[step.modifier] || '↑';
}

export function fmtDistance(m) {
  if (m >= 1000) return `${(m / 1000).toFixed(1)} 公里`;
  if (m >= 100) return `${Math.round(m / 50) * 50} 公尺`;
  return `${Math.max(10, Math.round(m / 10) * 10)} 公尺`;
}

// stage: far / near -> "前方 300 公尺，右轉進入光復路二段"; now -> "右轉"
export function maneuverSpeech(step, distanceM, stopName, stage) {
  const text = maneuverText(step, stopName).replace(/ /g, '');
  if (stage === 'now') return step.type === 'arrive' ? `${text}。` : `${text.split('進入')[0]}。`;
  return `前方 ${fmtDistance(distanceM).replace(' ', '')}，${text}。`;
}

// Hand-off to the phone's own navigation app. lat,lng of the next stop.
export function navigationUrl([lat, lng]) {
  const apple = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  return apple ? `https://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`
    : `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
}

export function offerSpeech(order) {
  return `新訂單，${order.restaurant}，${order.distance_km} 公里，${order.fee} 元。`;
}
