// World map widget for the Info panel.
//
// Uses Leaflet with L.CRS.Simple so the LLM-supplied (longitude, latitude)
// pairs -- which are arbitrary "world units" in roughly [-50, 50], NOT real
// earth degrees -- can be used directly as map coordinates. There are no
// tiles: the map is a plain dark canvas with markers + polylines drawn on
// top, which fits the existing dark UI and avoids the cost of a tile layer.
//
// Two render modes:
//   * Region view: every top-level settlement as a circle marker, with
//     LocationConnections drawn as polylines between them. Clicking a
//     marker drills into:
//   * Settlement view: that settlement's sub-locations as circle markers,
//     coloured by typology. A back button returns to the region view.

let _map = null;             // active Leaflet map instance
let _layer = null;           // current feature layer (markers + lines)
let _payload = null;         // last { locations, connections } we rendered
let _onBack = null;          // bound back-button handler

const SETTLEMENT_TYPES = new Set([
    'city', 'town', 'village', 'outpost', 'hamlet',
]);

// Sub-location typology -> marker colour. Keys mirror the constrained set
// the LOCATIONS_BATCH prompt enforces; anything else falls through to the
// neutral grey so a stray LLM type doesn't make the marker invisible.
const SUB_TYPE_COLORS = {
    building:   '#34d399',
    landmark:   '#f59e0b',
    district:   '#60a5fa',
    wilderness: '#84cc16',
    dungeon:    '#ef4444',
    road_node:  '#a78bfa',
};
const SUB_TYPE_DEFAULT_COLOR = '#94a3b8';

// Leaflet's bindTooltip / bindPopup accept HTML strings, so any
// LLM-supplied name passed in raw becomes an XSS sink. _safe() escapes
// the five HTML metacharacters and is reused by every tooltip and the
// popup builder below.
function _safe(s) {
    return s == null ? '' : String(s).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

// Connection typology -> polyline colour. Roads/paths/rivers/sea routes
// each get a distinct hue so the map reads at a glance.
const CONNECTION_COLORS = {
    road:      '#cbd5e1',
    path:      '#a3a3a3',
    river:     '#38bdf8',
    sea_route: '#0ea5e9',
};

// Geographic-feature typology -> { stroke, fill } colours. Polygon
// features use both; line features (river, coast) use only stroke. Fills
// are kept low-alpha so multiple overlapping features remain readable
// against the dark canvas without drowning out the markers / roads on top.
const FEATURE_STYLES = {
    forest:         { stroke: '#166534', fill: '#22c55e' },
    mountain_range: { stroke: '#78716c', fill: '#a8a29e' },
    hills:          { stroke: '#a16207', fill: '#ca8a04' },
    plains:         { stroke: '#65a30d', fill: '#bef264' },
    desert:         { stroke: '#b45309', fill: '#fbbf24' },
    swamp:          { stroke: '#365314', fill: '#4d7c0f' },
    lake:           { stroke: '#0369a1', fill: '#38bdf8' },
    river:          { stroke: '#38bdf8', fill: null    },
    coast:          { stroke: '#0ea5e9', fill: null    },
};
const FEATURE_DEFAULT_STYLE = { stroke: '#475569', fill: '#64748b' };

// Internal: tear down whichever feature layer is currently mounted so a
// fresh render starts from a clean state. Called between mode switches
// and on full re-render.
function _clearLayer() {
    if (_layer && _map) {
        _layer.clearLayers();
        _map.removeLayer(_layer);
    }
    _layer = null;
}

// Internal: ensure a Leaflet map exists in #worldMap. Re-uses the same
// instance across renders so pan/zoom state isn't reset on every refresh.
function _ensureMap() {
    if (_map) return _map;
    if (typeof L === 'undefined') {
        console.warn('Leaflet not loaded; skipping world map render.');
        return null;
    }
    const el = document.getElementById('worldMap');
    if (!el) return null;
    // maxZoom is set high (8) because settlement view fits a ~4-unit
    // bounding box (sub-locations cluster within +/- 2 units of their
    // parent). With L.CRS.Simple, zoom Z renders 1 world unit as 2^Z
    // pixels, so we need at least zoom 6-7 for those clusters to spread
    // across the canvas instead of bunching at the center; the extra
    // headroom lets the player wheel-zoom further if they want.
    _map = L.map(el, {
        crs: L.CRS.Simple,
        minZoom: -4,
        maxZoom: 8,
        zoomControl: true,
        attributionControl: false,
        zoomSnap: 0.25,
    });
    return _map;
}

// Internal: convert LLM-supplied (longitude, latitude) world units to a
// Leaflet LatLng. With L.CRS.Simple, the first coordinate is treated as
// "y" and the second as "x"; we use latitude as y and longitude as x so
// the on-screen orientation matches a standard map (north = up).
function _toLatLng(loc) {
    const lat = Number.isFinite(loc.latitude) ? loc.latitude : 0;
    const lng = Number.isFinite(loc.longitude) ? loc.longitude : 0;
    return L.latLng(lat, lng);
}

// Internal: fit the map viewport to the bounding box of the given points
// with a small padding. When fewer than two points exist we fall back to
// a fixed view centred on the origin so the map isn't a single pixel.
// ``maxZoom`` caps how far ``fitBounds`` will zoom in when the bounding
// box is small -- region view keeps it conservative so the whole world
// fits, settlement view passes a higher cap so the tight sub-location
// cluster actually spreads across the canvas.
function _fitToPoints(points, maxZoom) {
    if (!_map) return;
    const cap = Number.isFinite(maxZoom) ? maxZoom : 3;
    if (!points || points.length === 0) {
        _map.setView([0, 0], 0);
        return;
    }
    if (points.length === 1) {
        _map.setView(points[0], Math.min(cap, 5));
        return;
    }
    const bounds = L.latLngBounds(points);
    _map.fitBounds(bounds, { padding: [24, 24], maxZoom: cap });
}

// Internal: build the popup HTML for a marker. Kept minimal -- the Info
// accordion below the map is the canonical place to read full descriptions.
function _popupHtml(loc) {
    const bits = [];
    bits.push(`<div class="world-map-popup-name">${_safe(loc.name) || 'Unnamed'}</div>`);
    if (loc.type) {
        bits.push(`<div class="world-map-popup-type">${_safe(loc.type)}</div>`);
    }
    if (loc.description) {
        bits.push(`<div class="world-map-popup-desc">${_safe(loc.description)}</div>`);
    }
    return bits.join('');
}

// Render the region view: every top-level settlement as a circle marker,
// LocationConnections as polylines between them. Markers carry a click
// handler that drills into ``_renderSettlement`` for that settlement.
function _renderRegion() {
    const map = _ensureMap();
    if (!map || !_payload) return;
    _clearLayer();
    _layer = L.layerGroup().addTo(map);

    const settlements = _payload.locations.filter(l => l.parent_id == null);
    const byId = new Map(settlements.map(s => [s.id, s]));
    const featurePoints = [];

    // Geography first so roads + markers paint over it. Closed features
    // (forest, mountain_range, lake, ...) become filled polygons; open
    // features (river, coast) become stroked polylines. Each feature
    // contributes its points to the bounds-fit calculation so a tight
    // settlement cluster doesn't crop the surrounding terrain off-screen.
    (_payload.features || []).forEach(feat => {
        const pts = (feat.points || [])
            .filter(p => Array.isArray(p) && p.length >= 2 &&
                Number.isFinite(p[0]) && Number.isFinite(p[1]))
            .map(p => L.latLng(p[1], p[0]));
        if (pts.length < 2) return;
        pts.forEach(p => featurePoints.push(p));
        const style = FEATURE_STYLES[feat.type] || FEATURE_DEFAULT_STYLE;
        let shape;
        if (feat.closed && pts.length >= 3) {
            shape = L.polygon(pts, {
                color: style.stroke,
                weight: 1.5,
                opacity: 0.7,
                fillColor: style.fill || style.stroke,
                fillOpacity: 0.18,
            });
        } else {
            // River / coast lines get a thicker stroke so they remain
            // legible when crossed by a road polyline of similar weight.
            shape = L.polyline(pts, {
                color: style.stroke,
                weight: feat.type === 'river' ? 3 : 2.5,
                opacity: 0.85,
            });
        }
        if (feat.name) {
            shape.bindTooltip(_safe(feat.name), {
                sticky: false,
                direction: 'center',
                className: 'world-map-feature-label',
            });
        }
        shape.addTo(_layer);
    });

    // Polylines first so markers paint on top of them.
    (_payload.connections || []).forEach(conn => {
        const a = byId.get(conn.from_location_id);
        const b = byId.get(conn.to_location_id);
        if (!a || !b) return;
        const color = CONNECTION_COLORS[conn.type] || CONNECTION_COLORS.road;
        const dash = conn.type === 'path' ? '4,6' : null;
        const line = L.polyline([_toLatLng(a), _toLatLng(b)], {
            color, weight: 2, opacity: 0.7, dashArray: dash,
        });
        if (conn.name) line.bindTooltip(_safe(conn.name), { sticky: true });
        line.addTo(_layer);
    });

    const points = [];
    settlements.forEach(s => {
        const ll = _toLatLng(s);
        points.push(ll);
        const marker = L.circleMarker(ll, {
            radius: 8,
            color: '#0f172a',
            weight: 2,
            fillColor: '#10b981',
            fillOpacity: 0.95,
        }).bindPopup(_popupHtml(s));
        marker.bindTooltip(_safe(s.name) || 'Unnamed', {
            permanent: true, direction: 'right', offset: [8, 0],
            className: 'world-map-label',
        });
        marker.on('click', () => _renderSettlement(s.id));
        marker.addTo(_layer);
    });

    _setHeader('Region', false);
    // Include feature outlines in the bounds-fit so terrain that extends
    // past the settlement cluster isn't clipped at the edge of the canvas.
    _fitToPoints(points.concat(featurePoints));
}

// Render the settlement view: one settlement's sub-locations, coloured by
// type. The parent settlement is also drawn (larger, hollow) so the player
// keeps a sense of scale relative to the surrounding region.
function _renderSettlement(settlementId) {
    const map = _ensureMap();
    if (!map || !_payload) return;
    const parent = _payload.locations.find(l => l.id === settlementId);
    if (!parent) return;
    _clearLayer();
    _layer = L.layerGroup().addTo(map);

    const children = _payload.locations.filter(l => l.parent_id === settlementId);
    const points = [];

    const parentLL = _toLatLng(parent);
    points.push(parentLL);
    L.circleMarker(parentLL, {
        radius: 10,
        color: '#10b981',
        weight: 2,
        fillColor: 'transparent',
        fillOpacity: 0,
    }).bindTooltip(_safe(parent.name) || 'Settlement', {
        permanent: true, direction: 'top', offset: [0, -10],
        className: 'world-map-label',
    }).addTo(_layer);

    children.forEach(c => {
        const ll = _toLatLng(c);
        points.push(ll);
        const fill = SUB_TYPE_COLORS[c.type] || SUB_TYPE_DEFAULT_COLOR;
        const marker = L.circleMarker(ll, {
            radius: 6,
            color: '#0f172a',
            weight: 1.5,
            fillColor: fill,
            fillOpacity: 0.95,
        }).bindPopup(_popupHtml(c));
        marker.bindTooltip(_safe(c.name) || 'Unnamed', {
            permanent: true, direction: 'right', offset: [6, 0],
            className: 'world-map-label world-map-label-sub',
        });
        marker.addTo(_layer);
    });

    _setHeader(parent.name || 'Settlement', true);
    // Allow fitBounds to zoom in well past the region-view cap so the
    // tight (+/- 2 unit) sub-location cluster spreads across the canvas
    // instead of bunching at the centre. Caller can still wheel-zoom
    // further (or out) up to the map's own min/max bounds.
    _fitToPoints(points, 7);
}

// Internal: update the header bar above the map (title text + back-button
// visibility). The back button is only shown in settlement view.
function _setHeader(title, showBack) {
    const $title = document.getElementById('worldMapTitle');
    const $back = document.getElementById('worldMapBackBtn');
    if ($title) $title.textContent = title;
    if ($back) $back.style.display = showBack ? '' : 'none';
}

// Public: (re)render the map for a seed. ``payload`` is the response from
// /api/world/<seed_id>/map: { locations: [...], connections: [...] }.
// Empty payloads hide the panel entirely so the Info column doesn't
// reserve space for a blank map before world-building has produced any
// locations.
export function renderWorldMap(payload) {
    const panel = document.getElementById('worldMapPanel');
    if (!panel) return;
    _payload = payload || { locations: [], connections: [] };
    const hasAny = (_payload.locations || []).length > 0;
    panel.style.display = hasAny ? '' : 'none';
    if (!hasAny) {
        _clearLayer();
        return;
    }

    // Bind the back button once. The handler always falls back to the
    // region view; subsequent calls just replace the active payload.
    if (_onBack === null) {
        const $back = document.getElementById('worldMapBackBtn');
        if ($back) {
            _onBack = () => _renderRegion();
            $back.addEventListener('click', _onBack);
        }
    }

    _renderRegion();
    // Leaflet sometimes mis-sizes its container when the parent column
    // was hidden during init (game-view starts hidden); a manual resize
    // tick after the panel is shown fixes the dead canvas region.
    if (_map) setTimeout(() => _map.invalidateSize(), 0);
}

// Public: clear any rendered map without tearing down the Leaflet
// instance. Called from resetGameViewPanels() so a fresh seed doesn't
// flash the previous world's geography.
export function clearWorldMap() {
    _payload = null;
    _clearLayer();
    const panel = document.getElementById('worldMapPanel');
    if (panel) panel.style.display = 'none';
}

