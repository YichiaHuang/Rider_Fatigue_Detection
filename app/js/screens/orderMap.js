// The map inside the order card: pickup, drop-off, the rider, and the route
// between them. Leaflet (app/vendor/leaflet, loaded by index.html as window.L)
// on OpenStreetMap tiles — no API key. If Leaflet or the tiles can't load, the
// card simply has no map; nothing else depends on it.
//
// One map for the app's lifetime: the order card is rebuilt when the order
// changes, and each new card adopts this node instead of creating a new map.

import { el } from '../utils/dom.js';

const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const FIT_PADDING = [36, 36];
const FOLLOW_ZOOM = 17;

const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const pin = (cls, label) => window.L.divIcon({ className: '', html: `<span class="map-pin ${cls}">${label}</span>`, iconSize: [30, 30], iconAnchor: [15, 15] });

export function createOrderMap() {
  const node = el('div', { class: 'order-map', role: 'img', 'aria-label': '取餐點、送達點與路線地圖' });
  let map = null;
  let layers = null;
  let viewKey = null;
  let drawKey = null;
  let parent = null;  // the card currently holding the map
  let following = true;  // navigation: keep the rider centred until they drag the map away

  function ensureMap() {
    if (map || !window.L) return Boolean(map);
    map = window.L.map(node, { zoomControl: false, attributionControl: true });
    map.attributionControl.setPrefix(false);
    window.L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(map);
    layers = window.L.layerGroup().addTo(map);
    map.on('dragstart', () => { following = false; });
    return true;
  }

  return {
    node,

    recenter() { following = true; drawKey = null; },
    isFollowing: () => following,

    // Call after the card holding `node` is in the document (Leaflet needs a laid-out size).
    // follow: navigation view — stay on the rider instead of framing the whole trip.
    update({ order, route, position, follow = false }) {
      const drawable = order && order.pickup_pos && order.dropoff_pos && window.L && node.isConnected;
      node.hidden = !drawable;
      if (!drawable || !ensureMap()) return;
      // update() runs on every 1 s poll; redraw only when something on the map changed
      // (a new card counts: the node was re-parented and Leaflet must re-measure it).
      const nextDrawKey = JSON.stringify([order.id, order.state, route && [route.source, route.distance_m],
        position && [position.lat, position.lng], follow]);
      if (nextDrawKey === drawKey && node.parentNode === parent) return;
      drawKey = nextDrawKey;
      parent = node.parentNode;
      map.invalidateSize();

      const pickedUp = order.state === 'picked_up';
      layers.clearLayers();
      if (route) {
        window.L.polyline(route.geometry, {
          color: token('--series-1'), weight: 5, opacity: 0.85,
          dashArray: route.source === 'estimate' ? '4 10' : null,  // a guess must not look like a road
        }).addTo(layers);
      }
      if (!pickedUp) window.L.marker(order.pickup_pos, { icon: pin('pin-pickup', '取'), keyboard: false }).addTo(layers);
      window.L.marker(order.dropoff_pos, { icon: pin('pin-dropoff', '送'), keyboard: false }).addTo(layers);
      if (position) {
        window.L.circle([position.lat, position.lng], {
          radius: Math.min(position.accuracy || 0, 200), stroke: false, fillColor: token('--series-1'), fillOpacity: 0.15,
        }).addTo(layers);
        window.L.marker([position.lat, position.lng], { icon: pin('pin-rider', ''), keyboard: false, zIndexOffset: 500 }).addTo(layers);
      }

      if (follow && position) {
        viewKey = null;  // leaving navigation re-frames the whole trip
        if (following) map.setView([position.lat, position.lng], Math.max(map.getZoom() || 0, FOLLOW_ZOOM), { animate: true });
        return;
      }
      // Re-frame only when what is shown changes; following every GPS update
      // would fight a rider who has panned or zoomed the map.
      const key = `${order.id}:${order.state}:${route ? route.source : '-'}:${Boolean(position)}`;
      if (key === viewKey) return;
      viewKey = key;
      const points = [order.dropoff_pos];
      if (!pickedUp || !position) points.push(order.pickup_pos);
      if (position) points.push([position.lat, position.lng]);
      if (route) points.push(...route.geometry);
      map.fitBounds(window.L.latLngBounds(points), { padding: FIT_PADDING, maxZoom: 17, animate: false });
    },
  };
}
