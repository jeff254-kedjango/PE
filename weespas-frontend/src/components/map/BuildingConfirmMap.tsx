// BuildingConfirmMap — the "bad pin" tap-to-confirm picker.
//
// When a listing's dropped pin lands in a cluster the backend can't safely auto-pick, the
// listing OWNER confirms which building it is here. We render the candidate footprints on a
// small Leaflet/OSM map as faux-extruded 2.5D "blocks" (coloured cool→hot by their LIVE
// danger tier) so a TALLER building visibly stands taller — a picking cue the owner can use
// ("mine's the tall one"). Each also has a label chip (height / floors / distance). One tap
// selects; "Confirm" persists the choice.
//
// Why 2.5D-in-Leaflet, not WebGL 3D: the audience is low-end Kenyan/African phones. Real 3D
// (deck.gl/maplibre) needs a GPU and a heavy bundle — exactly where budget Androids struggle.
// These prisms are plain SVG polygons Leaflet already draws, so height is "seen" on ANY phone
// with no new engine and ~zero bundle cost (one map engine across the app — rule of cleanliness).
//
// Honesty: the tiers shown are the real per-building tiers (a CRITICAL candidate is shown
// red), so the owner isn't nudged toward a "nicer" building — they pick the RIGHT one. Height
// is coarse InSAR-derived data, so it's REPRESENTATIONAL (relative block height) — the exact
// metres live in the text chip, never implied as surveyed precision.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import Icon from '../ui/Icon';
import type { InsarCandidate } from '../../api/insar';
import './BuildingConfirmMap.css';

interface BuildingConfirmMapProps {
  candidates: InsarCandidate[];
  /** Persist the choice. Should resolve when the confirm mutation settles. */
  onConfirm: (insarBuildingId: number) => Promise<unknown> | void;
  /** True while the confirm request is in flight (disables the button). */
  confirming?: boolean;
}

// Tier index → outline/fill colour (mirrors RiskPill cool→hot). Unknown tier = slate.
const TIER_COLOR: Record<number, string> = {
  0: '#10b981', // stable
  1: '#84cc16', // low
  2: '#f59e0b', // elevated
  3: '#f97316', // high
  4: '#ef4444', // critical
};
const UNKNOWN_COLOR = '#64748b';

const TIER_LABEL: Record<number, string> = {
  0: 'Stable', 1: 'Low movement', 2: 'Elevated', 3: 'High', 4: 'Critical',
};

function colorFor(dangerLevel: number | null): string {
  return dangerLevel != null ? (TIER_COLOR[dangerLevel] ?? UNKNOWN_COLOR) : UNKNOWN_COLOR;
}

function describe(c: InsarCandidate): string {
  const bits: string[] = [];
  if (c.n_floors != null) bits.push(`${c.n_floors} floor${c.n_floors === 1 ? '' : 's'}`);
  else if (c.height_m != null) bits.push(`${Math.round(c.height_m)} m tall`);
  if (c.distance_m != null) bits.push(`${Math.round(c.distance_m)} m away`);
  return bits.join(' · ') || 'Building';
}

// ── 2.5D prism geometry ──────────────────────────────────────────────────────
// Height fallback chain: a building with NO height must never collapse to a flat,
// hard-to-tap slab. Prefer the real metres, else floors×3m, else a sane default — so
// every footprint becomes a solid, tappable block.
const FLOOR_HEIGHT_M = 3;
const DEFAULT_HEIGHT_M = 6;
const ELEV_SCALE_PX_PER_M = 1.6;  // pixels of "up" per metre — tuned for a 240px map
const MAX_ELEV_PX = 48;           // cap so one tall block can't dwarf the whole frame

function heightMetres(c: InsarCandidate): number {
  if (c.height_m != null && c.height_m > 0) return c.height_m;
  if (c.n_floors != null && c.n_floors > 0) return c.n_floors * FLOOR_HEIGHT_M;
  return DEFAULT_HEIGHT_M;
}

// Elevation in SCREEN pixels for a candidate (constant per zoom level), clamped.
function elevationPx(c: InsarCandidate): number {
  return Math.min(MAX_ELEV_PX, ELEV_SCALE_PX_PER_M * heightMetres(c));
}

// Darken a #rrggbb hex by `factor` (0..1) for the shaded wall faces — a cheap depth cue
// that needs no extra colour tokens (derived from the same tier colour as the roof).
function darken(hex: string, factor: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const r = Math.round(((n >> 16) & 0xff) * factor);
  const g = Math.round(((n >> 8) & 0xff) * factor);
  const b = Math.round((n & 0xff) * factor);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, '0')}`;
}

// Pull the first polygon's outer ring (lat/lng) from a GeoJSON geometry. MultiPolygon →
// first polygon; anything without a ≥3-vertex outer ring is skipped (returns null).
function outerRingLatLngs(geom: GeoJSON.Geometry): L.LatLng[] | null {
  let ring: GeoJSON.Position[] | undefined;
  if (geom.type === 'Polygon') ring = geom.coordinates[0];
  else if (geom.type === 'MultiPolygon') ring = geom.coordinates[0]?.[0];
  if (!ring || ring.length < 3) return null;
  // GeoJSON is [lng, lat]; Leaflet wants LatLng.
  return ring.map(([lng, lat]) => L.latLng(lat, lng));
}

const BuildingConfirmMap: React.FC<BuildingConfirmMapProps> = ({
  candidates, onConfirm, confirming,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  // One feature-group of prism faces (walls + roof) per building id, so we can rebuild
  // or restyle a single building without touching the others.
  const prismsRef = useRef<Map<number, L.FeatureGroup>>(new Map());
  const selectedRef = useRef<number | null>(null);  // stable mirror for click closures
  const [selected, setSelected] = useState<number | null>(null);

  // Only candidates that actually have a footprint geometry (with a valid outer ring)
  // can be shown/tapped — anything else is honestly omitted, never a silent slab.
  const drawable = useMemo(
    () => candidates.filter((c) => c.geometry != null
      && outerRingLatLngs(c.geometry as GeoJSON.Geometry) != null),
    [candidates],
  );

  // Build one 2.5D prism (shaded walls + bright roof) for a candidate at the CURRENT zoom.
  // Pixel elevation is converted back to LatLng so Leaflet re-projects the prism on pan
  // for free; only a zoom change invalidates it (handled by a zoomend rebuild below).
  const buildPrism = useCallback((map: L.Map, c: InsarCandidate): L.FeatureGroup | null => {
    const baseLL = outerRingLatLngs(c.geometry as GeoJSON.Geometry);
    if (!baseLL) return null;

    const dy = elevationPx(c);
    const basePts = baseLL.map((ll) => map.latLngToLayerPoint(ll));
    const topLL = basePts.map((p) => map.layerPointToLatLng(L.point(p.x, p.y - dy)));

    const roofColor = colorFor(c.danger_level);
    const wallColor = darken(roofColor, 0.62);
    const isSel = c.insar_building_id === selectedRef.current;

    const faces: L.Layer[] = [];
    // Walls: one quad per base edge (base_i → base_i+1 → top_i+1 → top_i).
    for (let i = 0; i < baseLL.length - 1; i++) {
      const quad = L.polygon([baseLL[i], baseLL[i + 1], topLL[i + 1], topLL[i]], {
        color: wallColor, weight: 1, fillColor: wallColor,
        fillOpacity: isSel ? 0.85 : 0.6, interactive: true,
      });
      faces.push(quad);
    }
    // Roof: brightest face, drawn last so it sits on top.
    const roof = L.polygon(topLL, {
      color: isSel ? '#ffffff' : darken(roofColor, 0.85),
      weight: isSel ? 3 : 1.5,
      fillColor: roofColor,
      fillOpacity: isSel ? 0.92 : 0.7,
      interactive: true,
    });
    faces.push(roof);

    const group = L.featureGroup(faces);
    const tierTxt = c.danger_level != null
      ? (TIER_LABEL[c.danger_level] ?? 'Unrated')
      : 'Unrated';
    group.bindTooltip(`${tierTxt} · ${describe(c)}`, { direction: 'top', sticky: true });
    group.on('click', () => setSelected(c.insar_building_id));
    return group;
  }, []);

  // (Re)draw every prism — used on first build, candidate change, and zoomend.
  const renderPrisms = useCallback((map: L.Map) => {
    prismsRef.current.forEach((g) => g.remove());
    prismsRef.current.clear();
    // Paint shortest-first so taller blocks overlap shorter ones naturally (depth order).
    [...drawable]
      .sort((a, b) => elevationPx(a) - elevationPx(b))
      .forEach((c) => {
        const prism = buildPrism(map, c);
        if (prism) {
          prism.addTo(map);
          prismsRef.current.set(c.insar_building_id, prism);
        }
      });
  }, [drawable, buildPrism]);

  // Build the map once; (re)draw prisms when the candidate set changes; rebuild on zoom.
  useEffect(() => {
    if (!containerRef.current || drawable.length === 0) return;

    const map = mapRef.current ?? L.map(containerRef.current, {
      zoomControl: false,
      attributionControl: false,
      scrollWheelZoom: false,
    });
    if (!mapRef.current) {
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 })
        .addTo(map);
      mapRef.current = map;
    }

    // Frame all candidates (use the flat footprints for honest bounds) before drawing.
    const footprints = drawable
      .map((c) => outerRingLatLngs(c.geometry as GeoJSON.Geometry))
      .filter((r): r is L.LatLng[] => r != null);
    const bounds = L.latLngBounds(footprints.flat());
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [32, 56], maxZoom: 19 });

    renderPrisms(map);

    // Pixel elevation is zoom-dependent, so rebuild the prisms after each zoom settles.
    const onZoom = () => renderPrisms(map);
    map.on('zoomend', onZoom);

    return () => {
      // Tear down fully on unmount so a remount (the modal re-opening) never double-inits.
      map.off('zoomend', onZoom);
      map.remove();
      mapRef.current = null;
      prismsRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawable, renderPrisms]);

  // On selection change, rebuild prisms so the chosen building gets its highlighted faces
  // (white roof outline + brighter fill). selectedRef keeps click closures correct without
  // recreating the map. Cheap: ≤6 small prisms.
  useEffect(() => {
    selectedRef.current = selected;
    const map = mapRef.current;
    if (map) renderPrisms(map);
  }, [selected, renderPrisms]);

  if (drawable.length === 0) {
    return (
      <p className="bcm__empty">
        <Icon name="mapPin" size={14} /> No nearby buildings to choose from.
      </p>
    );
  }

  return (
    <div className="bcm">
      <div ref={containerRef} className="bcm__map" role="application"
           aria-label="Tap your building on the map" />

      <ul className="bcm__list">
        {drawable.map((c) => {
          const isSel = c.insar_building_id === selected;
          return (
            <li key={c.insar_building_id}>
              <button
                type="button"
                className={`bcm__option${isSel ? ' is-selected' : ''}`}
                onClick={() => setSelected(c.insar_building_id)}
                aria-pressed={isSel}
              >
                <span className="bcm__swatch" style={{ background: colorFor(c.danger_level) }} />
                <span className="bcm__option-text">{describe(c)}</span>
                {isSel && <Icon name="check" size={16} />}
              </button>
            </li>
          );
        })}
      </ul>

      <button
        type="button"
        className="btn btn-primary bcm__confirm"
        disabled={selected == null || confirming}
        onClick={() => selected != null && onConfirm(selected)}
      >
        {confirming ? 'Confirming…' : 'Confirm this building'}
      </button>
    </div>
  );
};

export default BuildingConfirmMap;
