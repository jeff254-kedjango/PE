/**
 * RiskMap — single-screen dashboard.
 *
 * Performance model:
 *   - One bundle fetch per AOI per session (cached). Slider/play/click never
 *     refetch.
 *   - Geometry is uploaded to the GPU once per AOI via deck.gl's binary data
 *     path (no per-feature objects).
 *   - Slider tick = one ref update + one `updateTriggers` flip. The color
 *     accessor reads from a typed array (O(1) per polygon, no allocations).
 *   - Building click → O(1) index lookup via `byBuildingId` Map.
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { Map as MlMap } from "maplibre-gl";
import { Protocol } from "pmtiles";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { SolidPolygonLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import "maplibre-gl/dist/maplibre-gl.css";

import { Bundle, Classification, velocityAt, displacementAt, coherenceAt, horizontalVelocityAt, buildingSeries, blockPolygon, buildingCentroid } from "../lib/bundle";
import { ApiError, AoiSummary, useAoiBundle, useAoiRegistry } from "../lib/useAois";
import { initTelemetryFromUrl, meterBuildingView } from "../lib/telemetry";
import { useShopsOnMap, ShopOnMap } from "../lib/useShopsOnMap";
import { postContact, subscribeContact, CONTACT_GLOW_DEFAULT_MS } from "../lib/contact";
import { ThreatSidebar } from "./ThreatSidebar";
import { TopBar, ViewMode } from "./TopBar";
import { TimeSlider } from "./TimeSlider";

// Color-ramp saturation bounds, tuned to the REAL InSAR value range across all 5
// AOIs (measured from data/demo.duckdb: vertical velocity spans ≈ −9.9..+8.7 mm/yr,
// |east-west drift| ≤ ≈5.4 mm/yr). The old synthetic-era bounds (−25 / ±15) left
// the worst real subsidence at mid-amber and real drift near flat grey — the red /
// colored ends were never reached. These display-only bounds make the on-screen
// spread reflect the data; re-derive if the AOI set changes.
const VELOCITY_FLOOR_MM_YR = -10; // subsidence ramp: most-red (≈ real min, clips beyond)
const VELOCITY_CEIL_MM_YR  =  +5; // subsidence ramp: map to green (uplift)
const EW_VELOCITY_BOUND    = 5;   // drift ramp: ±5 mm/yr saturates (≈ real |drift| max)
const ELEVATION_GAIN = 1.0;       // true scale — buildings extrude at their real
                                  // measured height (Google Open Buildings), so the
                                  // 3D matches satellite/3D basemaps 1:1.

/**
 * Drift visualization: in Drift mode, each building's rendered footprint is
 * translated east-west by an amount proportional to (fused_height × ew_velocity).
 * This is *not* a literal physical projection — SolidPolygonLayer extrudes
 * straight up and can't tilt — but it conveys "structural displacement direction
 * and magnitude" at a glance. Buildings under heavy westward drift jump west
 * relative to their footprint; the gap is the visual cue.
 *
 * The scalar below is tuned so a 10 m tall building with ±10 mm/yr drift
 * shifts ~3 meters on the map at zoom 15.5. Adjust to taste.
 */
const DRIFT_VISUAL_GAIN_M_PER_MM_PER_M = 0.03;

// Selection chrome palette. Selection is UI, not data, so it uses the reserved
// chrome colors (white + signal-cyan #22d3ee, the same cyan used by buttons and
// labels) — NEVER the red/amber/green data ramp. A clicked building has its own
// fill recoloured to this glow (white↔cyan pulse, settling to white) directly in
// the per-vertex color buffer — the building itself lights up, no overlay object.
// The reserved white reads clearly even over an already-red (high-risk)
// building: the glow is orthogonal to the data ramp.
const SELECT_WHITE: [number, number, number] = [255, 255, 255];
const SELECT_CYAN:  [number, number, number] = [34, 211, 238]; // signal-cyan #22d3ee

// §8.1b pair-radiate "connected" glow. A SECOND transient set, distinct from BOTH the selection
// cyan AND the red/amber/green data ramp AND the amber shop pins — fuchsia is off every one of
// those, so a "we're connected" building can never be misread as a risk level, a selection, or a
// shop marker (the same chrome-is-orthogonal-to-data honesty rule the selection palette follows).
// It breathes on the same (1-cos) curve as selection, then TTL-decays back to the data ramp — no
// "contact ended" bookkeeping, so a closed tab never leaves a stuck glow. dim→bright with the
// pulse amplitude; the buildings glowed are the buyer's OWN (from the POST response) and the shop
// the OTHER party's pin resolves to (from an SSE pulse).
const CONTACT_DIM:    [number, number, number] = [122, 63, 127];  // #7a3f7f (amp 0)
const CONTACT_BRIGHT: [number, number, number] = [232, 121, 249]; // #e879f9 fuchsia-400 (amp 1)

// §8.1a shop-pin palette. Deliberately OFF the data ramp (red/amber/green) and off the
// selection cyan, so a shop marker never reads as a risk level or a selection. Amber-tinted
// gold — a neutral "point of interest" that stays legible over the dark basemap AND over an
// already-coloured (red/green) building. A CONFIRMED shop (its footprint carries a recorded
// structural assessment) gets a brighter ring + a small shield glyph — provenance, NOT a
// safety claim (same honest meaning as the sidebar "Confirmed" shield).
const SHOP_PIN:           [number, number, number] = [250, 204, 21];  // amber-300 (#facc15)
const SHOP_PIN_CONFIRMED: [number, number, number] = [253, 224, 71];  // amber-200 (#fde047), brighter

/** A shop resolved onto the map: its meta, the bundle row of the building it sits on (so a
 *  click reuses the existing selection glow + sidebar), and the footprint centroid to pin at. */
type ShopPin = {
  shop: ShopOnMap;
  row: number;
  position: [number, number];
};

// Click-feedback animation timing. The selected building breathes slowly and
// continuously between white and signal-cyan — one full white→cyan→white cycle
// per period — so a selection stays visibly "live" while inspected. The loop
// only runs while something is selected, and each frame is O(1) (it rewrites
// just the selected building's vertices, not the whole buffer).
const SELECT_PULSE_PERIOD_MS = 3000;   // one white↔cyan↔white cycle

/** Selection-halo style for a given pulse amplitude (`amp` ∈ [0,1]).
 *  amp drives the attention breath; at amp=0 this is the STEADY halo a
 *  selection settles into — a crisp white inner edge + a soft cyan outer glow.
 *  As amp rises the edges widen, the glow brightens, and the inner edge
 *  crossfades toward cyan, producing the white↔cyan shimmer on click. */
type HaloStyle = {
  inner: [number, number, number, number];
  outer: [number, number, number, number];
  innerWidth: number;
  outerWidth: number;
};
function selectHalo(amp: number): HaloStyle {
  const lerp = (a: number, b: number, t: number) => Math.round(a + (b - a) * t);
  return {
    inner: [
      lerp(SELECT_WHITE[0], SELECT_CYAN[0], amp),
      lerp(SELECT_WHITE[1], SELECT_CYAN[1], amp),
      lerp(SELECT_WHITE[2], SELECT_CYAN[2], amp),
      255,
    ],
    outer: [SELECT_CYAN[0], SELECT_CYAN[1], SELECT_CYAN[2], Math.round(130 + 80 * amp)],
    innerWidth: 2 + 4 * amp,
    outerWidth: 5 + 6 * amp,
  };
}

/** §8.1b pair-radiate fill colour for a given pulse amplitude (`amp` ∈ [0,1]): fuchsia
 *  interpolated dim→bright, breathing on the same (1-cos) curve as the selection halo so a
 *  contact glow and a selection glow pulse in phase. Returns rgb only (contact fill is always
 *  fully opaque; alpha is written separately). */
function contactGlow(amp: number): [number, number, number] {
  const lerp = (a: number, b: number, t: number) => Math.round(a + (b - a) * t);
  return [
    lerp(CONTACT_DIM[0], CONTACT_BRIGHT[0], amp),
    lerp(CONTACT_DIM[1], CONTACT_BRIGHT[1], amp),
    lerp(CONTACT_DIM[2], CONTACT_BRIGHT[2], amp),
  ];
}


export function RiskMap() {
  const { aois, error: regErr } = useAoiRegistry();
  const [activeCode, setActiveCode] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("subsidence");

  // Deep-link target from a Weespas "View on risk map" link (?aoi=&building=), read
  // once on mount. Also captures + strips the telemetry token (?wt). Null on a normal
  // load, so this is inert for ordinary visitors.
  const deepLink = useRef(initTelemetryFromUrl());

  // Seed activeCode once we have the registry — honour a deep-linked AOI if it exists,
  // else default to the first AOI (the prior behaviour).
  useEffect(() => {
    if (activeCode || !aois || !aois.length) return;
    const wanted = deepLink.current.aoi;
    const hit = wanted && aois.some(a => a.aoi_code === wanted) ? wanted : aois[0].aoi_code;
    setActiveCode(hit);
  }, [activeCode, aois]);

  const { bundle, error: bundleErr } = useAoiBundle(activeCode);
  const activeAoi = aois?.find(a => a.aoi_code === activeCode) ?? null;

  if (regErr) return <ErrorPanel err={regErr} where="/aois" />;
  if (bundleErr) return <ErrorPanel err={bundleErr} where={`bundle for ${activeCode}`} />;

  return (
    <div className="h-screen w-screen flex flex-col lg:flex-row bg-ink-950 text-slate-200 font-mono select-none">
      <MapPane
        aois={aois}
        activeCode={activeCode}
        setActiveCode={setActiveCode}
        bundle={bundle}
        activeAoi={activeAoi}
        mode={mode}
        onModeChange={setMode}
        deepLinkBuilding={deepLink.current.building}
      />
    </div>
  );
}


/* Zoom bounds for the map + its buttons. ZOOM_EPS keeps the "at the limit"
 * test off the exact float edge so a button reliably disables on the last step. */
const MIN_ZOOM = 11;
const MAX_ZOOM = 19;
const ZOOM_EPS = 0.01;


/**
 * Icon-only map zoom control. Stacked +/− buttons that drive MapLibre's native
 * `zoomIn`/`zoomOut` (smooth ±1 step, camera-preserving; the deck.gl overlay
 * re-syncs automatically). `React.memo` + stable `useCallback` handlers mean
 * this never reconciles during slider scrubbing or playback ticks — the parent
 * re-renders on every animation frame, and this subtree is skipped entirely.
 * It DOES reconcile when `canZoomIn`/`canZoomOut` flip at a zoom limit.
 */
const ZoomControl = memo(function ZoomControl({
  onZoomIn, onZoomOut, canZoomIn, canZoomOut,
}: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  canZoomIn: boolean;
  canZoomOut: boolean;
}) {
  // Glass-morphism chip: a translucent ink fill with `backdrop-blur` frosts
  // whatever map is behind it, a hairline white border + black ring read the
  // edge against both light and dark basemaps, and an inner top highlight gives
  // the glass its lit-from-above curve. `active:` presses the button in;
  // `disabled:` greys it out and blocks the pointer at a zoom limit. Hover keeps
  // the shared signal-cyan accent used by the other map buttons.
  const btn =
    "relative w-9 h-9 grid place-items-center text-slate-100 transition " +
    "bg-white/5 backdrop-blur-md hover:bg-white/10 " +
    "before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-white/20 " +
    "hover:text-signal-cyan active:scale-95 active:bg-signal-cyan/20 " +
    "disabled:opacity-30 disabled:pointer-events-none disabled:text-slate-500";
  return (
    <div className="absolute bottom-24 right-4 pointer-events-auto flex flex-col divide-y divide-white/10 overflow-hidden rounded-lg border border-white/10 ring-1 ring-black/40 shadow-lg shadow-black/40">
      <button type="button" aria-label="Zoom in" onClick={onZoomIn} disabled={!canZoomIn} className={btn}>
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M7 2v10M2 7h10" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" />
        </svg>
      </button>
      <button type="button" aria-label="Zoom out" onClick={onZoomOut} disabled={!canZoomOut} className={btn}>
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M2 7h10" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
});


function MapPane({
  aois, activeCode, setActiveCode, bundle, activeAoi, mode, onModeChange, deepLinkBuilding,
}: {
  aois: AoiSummary[] | null;
  activeCode: string | null;
  setActiveCode: (c: string) => void;
  bundle: Bundle | null;
  activeAoi: AoiSummary | null;
  mode: ViewMode;
  onModeChange: (m: ViewMode) => void;
  deepLinkBuilding: number | null;
}) {
  const mapContainer = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);

  /** Current month index. Lives in a ref so slider scrubbing is allocation-free. */
  const monthIdxRef = useRef(0);
  const [monthIdx, setMonthIdxState] = useState(0); // mirror for React-driven UI
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  // Bumped exactly once when we focus a building from a Weespas deep-link (never on an
  // ordinary map click or WATCH-stepper select) — drives the sidebar's scroll-to-analysis.
  const [deepLinkFocusNonce, setDeepLinkFocusNonce] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [showBlocks, setShowBlocks] = useState(false);  // C3 — block overlay toggle
  const [showWatch, setShowWatch] = useState(false);    // WATCH (MIXED_SIGNAL) cohort overlay
  // Zoom-button enabled state, kept in sync with the map's actual zoom (see the
  // 'zoom' listener in map-init). A button greys out + goes inert at its limit.
  const [canZoomIn, setCanZoomIn] = useState(true);
  const [canZoomOut, setCanZoomOut] = useState(true);

  /* ---- §8.1a shops-on-map: which footprints in this AOI are commerce shops. INERT without a
   * Weespas telemetry token (anonymous visitors get an empty layer — the base map is unchanged).
   * `partial` = the commerce read degraded; the map still renders, we just note shops may be
   * incomplete. See lib/useShopsOnMap.ts. */
  const { shops, partial: shopsPartial } = useShopsOnMap(activeCode);

  /* ---- Idle risk-rank: percentile rank of collapse_score WITHIN this AOI.
   * This is the WITHIN-AOI lens — it spreads buildings across the full colour
   * range so the worst structures in *this* neighbourhood always stand out,
   * whatever the absolute level. The cross-AOI ABSOLUTE lens is danger_level
   * (the badge tier), shown alongside; the two are deliberately never conflated.
   * Computed once per bundle: O(n log n) sort here, then O(1) lookup per building
   * at paint. riskRank[i] ∈ [0,1]; 1 = highest-risk in this AOI. */
  const riskRank = useMemo<Float32Array | null>(() => {
    if (!bundle) return null;
    const risk = bundle.compositeRisk;
    const n = risk.length;
    const order = Array.from({ length: n }, (_, i) => i);
    order.sort((a, b) => risk[a] - risk[b]);
    const rank = new Float32Array(n);
    // Average-rank for ties so a flat plateau of equal risk shares one colour
    // rather than fanning across the ramp by array order.
    let i = 0;
    while (i < n) {
      let j = i;
      while (j + 1 < n && risk[order[j + 1]] === risk[order[i]]) j++;
      const t = n > 1 ? (i + j) / 2 / (n - 1) : 0;
      for (let k = i; k <= j; k++) rank[order[k]] = t;
      i = j + 1;
    }
    return rank;
  }, [bundle]);

  /* ---- Map init (once) -------------------------------------------------- */
  useEffect(() => {
    if (!mapContainer.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: emptyDarkStyle(),
      center: [36.874, -1.251],
      zoom: 15.5,
      // Bound zoom so the +/− buttons have real limits to disable against. The
      // AOIs are ~2 km tiles: below ~11 the building layer is meaningless, above
      // ~19 we're past the data's spatial resolution. MIN/MAX_ZOOM mirror these.
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      pitch: 55,
      bearing: -20,
      attributionControl: false,
    });
    mapRef.current = map;

    // Drive the zoom buttons' enabled state. Fires only while zooming; each
    // handler bails unless the boolean actually flips, so setState (and the
    // memoized ZoomControl) reconcile only when a limit is crossed — not on
    // every frame of a zoom animation. O(1) per event.
    const syncZoomLimits = () => {
      const z = map.getZoom();
      setCanZoomIn(z < MAX_ZOOM - ZOOM_EPS);
      setCanZoomOut(z > MIN_ZOOM + ZOOM_EPS);
    };
    map.on("zoom", syncZoomLimits);
    syncZoomLimits();

    const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
    overlayRef.current = overlay;
    map.addControl(overlay as unknown as maplibregl.IControl);

    // Probe for an installed PMTiles basemap. If present, upgrade the style
    // from the flat dark canvas to the full Protomaps schema.
    //
    // Why not just `if (res.ok)`: the Vite dev server (and most SPA hosts)
    // returns index.html with status 200 for missing static files so that
    // client-side routing can take over. A bare `res.ok` check therefore
    // happily tries to install a PMTiles source on top of an HTML body,
    // and MapLibre throws "Wrong magic number for PMTiles archive".
    //
    // Fix: read the first 7 bytes and verify the actual PMTiles magic
    // string. Tiny range-request — costs nothing if the file is real, and
    // correctly bails out when the response is the SPA fallback.
    (async () => {
      try {
        const res = await fetch("/tiles/nairobi.pmtiles", {
          headers: { Range: "bytes=0-6" },
        });
        if (!res.ok) return;
        const head = new Uint8Array(await res.arrayBuffer());
        // ASCII "PMTiles" = 0x50 0x4D 0x54 0x69 0x6C 0x65 0x73
        const MAGIC = [0x50, 0x4d, 0x54, 0x69, 0x6c, 0x65, 0x73];
        if (head.length < 7 || !MAGIC.every((b, i) => head[i] === b)) return;
        map.setStyle(protomapsDarkStyle());
      } catch {
        /* offline / no tiles installed — keep the empty dark canvas */
      }
    })();

    return () => {
      map.off("zoom", syncZoomLimits);
      map.remove();
      maplibregl.removeProtocol("pmtiles");
    };
  }, []);

  /* ---- Recenter when AOI changes --------------------------------------- */
  useEffect(() => {
    if (!mapRef.current || !activeAoi) return;
    mapRef.current.flyTo({
      center: [activeAoi.center_lon, activeAoi.center_lat],
      zoom: 15.5, pitch: 55, bearing: -20, duration: 1200,
    });
    setSelectedRow(null);
    monthIdxRef.current = 0;
    setMonthIdxState(0);
  }, [activeAoi?.aoi_code]);

  /* ---- Build deck.gl binary data once per bundle -----------------------
   *
   * SolidPolygonLayer in binary mode wants:
   *   data: {
   *     length: <polygon count>,
   *     startIndices: Int32Array of length (n_polygons + 1), in VERTEX units,
   *     attributes: {
   *       getPolygon:   { value: Float32Array<lon,lat,...>, size: 2 },
   *       getElevation: { value: Float32Array of length n_vertices_total },  // per-VERTEX
   *       getFillColor: { value: Uint8Array of length n_vertices_total*4, size: 4 },
   *     }
   *   }
   *
   * Critical and easy to get wrong: per-vertex attributes must be sized to
   * TOTAL VERTEX COUNT, not polygon count. The previous code passed callback
   * accessors against binary data, which is undocumented and was rendering
   * nothing — that's why "Play" advanced the date but the screen stayed dark.
   * ------------------------------------------------------------------------ */
  const staticBinary = useMemo(() => {
    if (!bundle) return null;
    const n_polys    = bundle.header.n_buildings;
    const n_vertices = bundle.ringCoords.length / 2;   // (lon,lat) pairs

    // startIndices is in vertex units. ringOffsets is in lon-lat-float units.
    const startIndices = new Int32Array(bundle.ringOffsets.length);
    for (let i = 0; i < bundle.ringOffsets.length; i++) {
      startIndices[i] = bundle.ringOffsets[i] >> 1;   // /2
    }

    // Per-vertex elevation: each vertex of polygon i extruded to fused_height.
    // Fused = inverse-variance blend of floor-count estimate and InSAR phase
    // inversion — see backend/ARCHITECTURE_ONE.md, study 1.
    const elevation = new Float32Array(n_vertices);
    for (let i = 0; i < n_polys; i++) {
      const start = startIndices[i];
      const end   = startIndices[i + 1];
      const fused = bundle.fusedHeightM[i];
      const h = ((Number.isFinite(fused) && fused > 0 ? fused : (bundle.heightM[i] || 6))
                 * ELEVATION_GAIN);
      for (let v = start; v < end; v++) elevation[v] = h;
    }

    // Drift-skewed ringCoords: same length/shape as bundle.ringCoords, but
    // each vertex of polygon i is shifted east-west by an amount proportional
    // to (fused_height × ew_velocity_at_current_month × GAIN). Written by the
    // mode/month effect below; the actual values are stale here, which is OK
    // because the effect repopulates it before the layer reads it.
    const driftCoords = new Float32Array(bundle.ringCoords.length);
    driftCoords.set(bundle.ringCoords);

    // Per-vertex color buffer; values written by the month-tick effect below.
    const fillColor = new Uint8Array(n_vertices * 4);

    return { n_polys, n_vertices, startIndices, elevation, fillColor, driftCoords };
  }, [bundle]);

  /* ---- C1/C3 — block overlay polygons, precomputed once per bundle --------
   * One PolygonLayer feature per *non-empty* block. Each carries its grid-cell
   * ring plus the aggregate metrics, so deck.gl colors and the tooltip read
   * straight off the object. Empty blocks are dropped (no feature emitted).
   * ------------------------------------------------------------------------ */
  const blockData = useMemo(() => {
    if (!bundle) return [];
    const n = bundle.header.block_grid.n_blocks;
    const out: {
      polygon: [number, number][];
      worstVelocity: number;
      meanRisk: number;
      maxRisk: number;
      count: number;
      confirmed: number;
    }[] = [];
    for (let b = 0; b < n; b++) {
      const count = bundle.blockCount[b];
      if (count <= 0) continue;
      out.push({
        polygon: blockPolygon(bundle, b),
        worstVelocity: bundle.blockWorstVelocity[b],
        meanRisk: bundle.blockMeanRisk[b],
        maxRisk: bundle.blockMaxRisk[b],
        count,
        confirmed: bundle.blockConfirmed[b],
      });
    }
    return out;
  }, [bundle]);

  /* ---- WATCH cohort (MIXED_SIGNAL) — overlay polygons + worst-first order --
   * MIXED_SIGNAL = trustworthy-but-non-linear movers (accelerating/curving):
   * the closest InSAR analogue to a pre-collapse ground cue, and the cohort the
   * South C retrospective flagged as where the remaining value is. We don't
   * re-score them (the backend already ranks MIXED on its true composite, no
   * damp) — we just make them findable: an amber outline overlay + a worst-first
   * index list (by cohort_composite_pct desc, tie-broken by most-negative accel)
   * the sidebar stepper walks through. Footprints reuse ringCoords/ringOffsets,
   * same geometry source as the buildings layer. */
  const watchData = useMemo(() => {
    if (!bundle) return { polygons: [] as { polygon: [number, number][] }[], order: [] as number[] };
    const { ringCoords, ringOffsets, classification, cohortCompositePct, velocityAccelMmYr2 } = bundle;
    const n = bundle.header.n_buildings;
    const polygons: { polygon: [number, number][] }[] = [];
    const idx: number[] = [];
    for (let i = 0; i < n; i++) {
      if (classification[i] !== Classification.MIXED_SIGNAL) continue;
      idx.push(i);
      const s = ringOffsets[i] >> 1;      // vertex units (ringOffsets is in float units)
      const e = ringOffsets[i + 1] >> 1;
      const polygon: [number, number][] = [];
      for (let v = s; v < e; v++) polygon.push([ringCoords[v * 2], ringCoords[v * 2 + 1]]);
      if (polygon.length >= 3) polygons.push({ polygon });
    }
    // Worst-first: highest cohort composite percentile, then most-negative accel
    // (most strongly accelerating). NaN accel sorts last (treated as +inf).
    const order = idx.slice().sort((a, b) => {
      const pc = cohortCompositePct[b] - cohortCompositePct[a];
      if (pc !== 0) return pc;
      const aa = Number.isFinite(velocityAccelMmYr2[a]) ? velocityAccelMmYr2[a] : Infinity;
      const bb = Number.isFinite(velocityAccelMmYr2[b]) ? velocityAccelMmYr2[b] : Infinity;
      return aa - bb;
    });
    return { polygons, order };
  }, [bundle]);

  /* ---- §8.1a shop pins: resolve each shop to a map point via the footprint ALREADY in the
   * bundle (O(1) building_id→row lookup, then a centroid of that row's ring). A shop whose
   * building isn't in this AOI's bundle is silently dropped (nothing to anchor to). The pin
   * carries the shop's row so a click can drive the EXISTING selection glow + sidebar with no
   * new highlight code. Empty when there are no shops (byte-identical to today's map). */
  const shopPins = useMemo(() => {
    if (!bundle || shops.length === 0) return [] as ShopPin[];
    const out: ShopPin[] = [];
    for (const s of shops) {
      const row = bundle.byBuildingId.get(s.insar_building_id);
      if (row == null) continue;                       // not in this bundle — un-anchorable
      const position = buildingCentroid(bundle, row);
      if (!position) continue;                          // degenerate ring — skip
      out.push({ shop: s, row, position });
    }
    return out;
  }, [bundle, shops]);

  /* ---- Focus a specific building (from the WATCH stepper): select it (drives
   * the glow + sidebar detail) and fly the camera to its footprint centroid. */
  const focusBuilding = useCallback((row: number) => {
    setSelectedRow(row);
    if (!bundle || !mapRef.current) return;
    const { ringCoords, ringOffsets } = bundle;
    const s = ringOffsets[row] >> 1;
    const e = ringOffsets[row + 1] >> 1;
    if (e - s < 1) return;
    let sx = 0, sy = 0;
    for (let v = s; v < e; v++) { sx += ringCoords[v * 2]; sy += ringCoords[v * 2 + 1]; }
    const cx = sx / (e - s), cy = sy / (e - s);
    mapRef.current.flyTo({ center: [cx, cy], zoom: 17, pitch: 55, bearing: -20, duration: 900 });
  }, [bundle]);

  /* ---- Deep-link fly-to: when arriving from a Weespas "View on risk map" link, once
   * the matching AOI's bundle is loaded, select + fly to the requested building (O(1)
   * id→row lookup). Runs once per (bundle, target) — `done` guards against re-firing on
   * unrelated re-renders. No-op for ordinary visits (deepLinkBuilding null). */
  const deepLinkDone = useRef(false);
  useEffect(() => {
    if (deepLinkDone.current || deepLinkBuilding == null || !bundle) return;
    const row = bundle.byBuildingId.get(deepLinkBuilding);
    if (row == null) return;            // not in this AOI's bundle — leave default view
    deepLinkDone.current = true;
    focusBuilding(row);
    // Arriving via "View Building Risk Analysis": bring the building's Structural Threat
    // analysis into view in the sidebar (the user came here for THIS building, not to browse).
    setDeepLinkFocusNonce((n) => n + 1);
  }, [bundle, deepLinkBuilding, focusBuilding]);

  /* ---- Commercial-usage telemetry: report each DISTINCT building the user inspects to
   * Weespas's metering spine (deduped + inert without a telemetry token — see
   * lib/telemetry.ts). This is the signal the §8 company-detection scorer consumes. */
  useEffect(() => {
    if (selectedRow == null || !bundle) return;
    meterBuildingView(bundle.buildingId[selectedRow], activeCode);
  }, [selectedRow, bundle, activeCode]);

  /* ---- §8.1b pair-radiate glow state. `contactRef` maps a bundle row → its glow expiry
   * (performance.now() ms); `contactAmpRef` holds the live pulse amp so a mid-life data repaint
   * paints the glow, not the true colour (mirrors `haloRef` for selection). `contactVersion` bumps
   * on membership change (a new glow added, or one expired) to (re)start the animation loop and
   * force the O(n) repaint to re-apply / release the fuchsia override. Rows live in a ref so the
   * per-frame paint is allocation-free; only membership changes touch React state.
   * `bundleRef` mirrors the live bundle so the once-mounted SSE handler + async POST callback
   * resolve building_ids against the CURRENT AOI without re-subscribing on every switch. */
  const contactRef = useRef<Map<number, number>>(new Map());
  const contactAmpRef = useRef(0);
  const [contactVersion, setContactVersion] = useState(0);
  const bundleRef = useRef<Bundle | null>(bundle);
  bundleRef.current = bundle;

  /** Add/refresh a fuchsia "connected" glow on the given bundle rows for `ttlMs`, then bump
   *  `contactVersion` to (re)start the animation loop and re-run the override repaint. O(rows).
   *  A row already glowing simply gets its TTL extended. Stable identity (refs + a stable setter),
   *  so the SSE subscription that depends on it mounts exactly once. */
  const addContactGlow = useCallback((rows: number[], ttlMs: number) => {
    if (rows.length === 0) return;
    const expiry = performance.now() + ttlMs;
    for (const row of rows) contactRef.current.set(row, expiry);
    setContactVersion(v => v + 1);
  }, []);

  /* ---- §8.1b pair-radiate BUYER half: opening a shop pin selects it (existing glow + sidebar)
   * AND registers a contact. The POST returns the buyer's OWN footprints in this AOI, which we
   * glow fuchsia LOCALLY from the response — consented by the tap, never via an SSE self-loop.
   * The shop's own footprint glows too, so the pair lights up together. Best-effort + inert
   * without a token: a failed/absent POST leaves the ordinary pin-select behaviour intact. */
  const openShopPin = useCallback((pin: ShopPin) => {
    setSelectedRow(pin.row);
    void postContact(pin.shop.shop_id, activeCode ?? "", pin.shop.insar_building_id).then(res => {
      if (!res) return;
      const b = bundleRef.current;
      if (!b) return;
      const rows: number[] = [];
      // The shop's own footprint (glows the pair's other half locally too).
      const shopRow = b.byBuildingId.get(pin.shop.insar_building_id);
      if (shopRow != null) rows.push(shopRow);
      // The buyer's own footprints in this AOI (server-resolved from the verified token).
      for (const bid of res.own_building_ids) {
        const row = b.byBuildingId.get(bid);
        if (row != null) rows.push(row);
      }
      addContactGlow(rows, res.glow_ttl_ms);
    });
  }, [activeCode, addContactGlow]);

  /* ---- §8.1b pair-radiate SELLER half: one long-lived SSE stream on this user's OWN channel.
   * A seller viewing the map sees an anonymized fuchsia pulse on THEIR shop's footprint when a
   * buyer opens its pin — the buyer's identity/coordinates never cross (privacy decision #2).
   * Mounts ONCE (stable deps): the handler resolves each pulse's building_id against the CURRENT
   * AOI via bundleRef, so switching AOIs never re-subscribes. Inert without a token; never throws.
   * A pulse for a building not in the currently-loaded AOI is simply dropped (nothing to glow). */
  useEffect(() => {
    const unsubscribe = subscribeContact(evt => {
      const b = bundleRef.current;
      if (!b) return;
      const row = b.byBuildingId.get(evt.shop_building_id);
      if (row == null) return; // pulse for a footprint not in the AOI on screen — nothing to glow
      addContactGlow([row], CONTACT_GLOW_DEFAULT_MS);
    });
    return unsubscribe;
  }, [addContactGlow]);

  /* ---- Selection glow animation: continuous slow white↔cyan breath --------
   * While a building is selected, it breathes between white and signal-cyan on
   * a SELECT_PULSE_PERIOD_MS loop (one full white→cyan→white cycle per period).
   *
   * Performance: the loop is O(1) per frame, INDEPENDENT of building count. It
   * does NOT re-run the heavy O(n) data repaint — it overwrites only the
   * selected building's handful of vertices in the shared fillColor buffer,
   * then bumps colorVersion so deck.gl re-uploads. The loop runs only while a
   * selection exists; deselecting cancels it and the data-repaint effect (which
   * depends on selectedRow) restores the building's true colour.
   *
   * haloRef holds the live colour so a data repaint mid-pulse paints the glow,
   * not the true colour, for that frame.
   * ------------------------------------------------------------------------ */
  const [colorVersion, setColorVersion] = useState(0);
  const haloRef = useRef<HaloStyle>(selectHalo(0));

  /* ---- Combined glow animation loop: while anything glows (a selection AND/OR one or more
   * pair-radiate contacts), breathe on the shared SELECT_PULSE_PERIOD_MS (1-cos) curve so
   * selection (cyan) and contact (fuchsia) pulse in phase. Each frame overwrites only the glowing
   * rows' vertices — O(selected + live contacts), independent of building count — then bumps
   * colorVersion so deck.gl re-uploads. A contact whose TTL elapses is dropped and its true data
   * colour is restored by the O(n) repaint (re-run via the contactVersion bump). The loop runs
   * only while something glows; deselecting / the last expiry lets it settle. */
  useEffect(() => {
    const hasSelection = selectedRow != null;
    const hasContacts = contactRef.current.size > 0;
    if ((!hasSelection && !hasContacts) || !bundle || !staticBinary) {
      haloRef.current = selectHalo(0);
      contactAmpRef.current = 0;
      return;
    }
    const { startIndices, fillColor } = staticBinary;
    // Reads rgb[0..2] only; alpha is always fully opaque for a glow. Accepts the 3-tuple
    // contact fuchsia and the 4-tuple selection halo.inner alike.
    const paintRow = (row: number, rgb: readonly number[]) => {
      const start = startIndices[row] * 4;
      const end   = startIndices[row + 1] * 4;
      for (let off = start; off < end; off += 4) {
        fillColor[off]     = rgb[0];
        fillColor[off + 1] = rgb[1];
        fillColor[off + 2] = rgb[2];
        fillColor[off + 3] = 255;
      }
    };
    let raf = 0;
    let t0 = 0;
    const tick = (ts: number) => {
      if (!t0) t0 = ts;
      // (1 - cos) sweeps 0→1→0 over one period.
      const phase = ((ts - t0) / SELECT_PULSE_PERIOD_MS) * Math.PI * 2;
      const amp = (1 - Math.cos(phase)) / 2;
      const halo = selectHalo(amp);
      haloRef.current = halo;
      contactAmpRef.current = amp;
      // Expire elapsed contacts. A released row must be restored to its true data colour, which
      // only the O(n) repaint knows — so flag the membership change to re-run it.
      let expired = false;
      for (const [row, deadline] of contactRef.current) {
        if (ts >= deadline) { contactRef.current.delete(row); expired = true; }
      }
      // Paint live glows. Selection (cyan) wins over contact (fuchsia) on a row that is both, so the
      // tapped structure reads as selected and is never double-painted.
      const glowRgb = contactGlow(amp);
      for (const row of contactRef.current.keys()) {
        if (row !== selectedRow) paintRow(row, glowRgb);
      }
      if (selectedRow != null) paintRow(selectedRow, halo.inner);
      setColorVersion(v => v + 1);
      if (expired) setContactVersion(v => v + 1);
      // Keep animating while anything still glows; otherwise settle (a deselect / last expiry
      // re-runs this effect via its deps and returns early without rescheduling).
      if (selectedRow != null || contactRef.current.size > 0) {
        raf = requestAnimationFrame(tick);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [selectedRow, bundle, staticBinary, contactVersion]);

  /* ---- Repaint the per-vertex color buffer when month, mode, or selection
   * changes. O(n_buildings) writes per tick, zero allocations. The same
   * Uint8Array is mutated in place and re-uploaded via updateTriggers.
   *
   * Mode dispatch is at the per-building level: each building's color is
   * driven by `velocity_mm_yr` (subsidence ramp) OR `velocity_horizontal_ew`
   * (drift ramp). Coherence desaturation applies to both ramps.
   * ------------------------------------------------------------------------ */
  useEffect(() => {
    if (!bundle || !staticBinary) return;
    const { n_polys, startIndices, fillColor } = staticBinary;
    const m = monthIdx;
    // Idle (timeline untouched): paint the cumulative VERDICT — each building's
    // composite-risk rank in this AOI — so the resting map is a risk heat-map,
    // not the deceptive all-green of month-0 velocity (nothing has moved yet).
    // Once the user plays or scrubs, switch to the per-epoch movement "story".
    const idle = m === 0 && !playing && riskRank != null;
    for (let i = 0; i < n_polys; i++) {
      const coh = coherenceAt(bundle, i, m);
      let r: number, g: number, b: number, a: number;
      if (idle) {
        [r, g, b, a] = riskRankToRGBA(riskRank![i], coh);
      } else if (mode === "subsidence") {
        const v = velocityAt(bundle, i, m);
        [r, g, b, a] = velocityToRGBA(v, coh);
      } else {
        const ew = horizontalVelocityAt(bundle, i, m);
        [r, g, b, a] = driftToRGBA(ew, coh);
      }
      // §8.1b pair-radiate: a "connected" building glows fuchsia. Applied BEFORE the selection
      // override so that if a row is both selected and a contact, selection (cyan) wins — the
      // tapped structure reads as selected, never double-painted.
      if (contactRef.current.has(i)) {
        const glow = contactGlow(contactAmpRef.current);
        r = glow[0]; g = glow[1]; b = glow[2]; a = 255;
      }
      // The selected building glows: override its own vertices with the live
      // animated white↔cyan selection colour so the clicked structure itself
      // lights up — no separate overlay object on top of it.
      if (i === selectedRow) {
        const glow = haloRef.current.inner;
        r = glow[0]; g = glow[1]; b = glow[2]; a = 255;
      }
      const start = startIndices[i] * 4;
      const end   = startIndices[i + 1] * 4;
      for (let off = start; off < end; off += 4) {
        fillColor[off]     = r;
        fillColor[off + 1] = g;
        fillColor[off + 2] = b;
        fillColor[off + 3] = a;
      }
    }
    setColorVersion(v => v + 1);
    // selectedRow is a dep so that on select we seed the glow colour and on
    // deselect we restore the building's true colour. The per-frame breathing
    // itself is driven by the O(1) animation loop above, not this O(n) repaint.
  }, [bundle, staticBinary, monthIdx, mode, playing, riskRank, selectedRow, contactVersion]);

  /* ---- Rebuild drift-skewed ringCoords when month or mode changes ---------
   * Subsidence mode: zero offset, drift coords identity-copy of upright.
   * Drift mode: offset each polygon's vertices by
   *     offset_m = fused_height × ew_velocity × VISUAL_GAIN
   * converted to lon-degrees at the polygon's latitude.
   *
   * O(total_vertices), one allocation-free pass. Could be skipped in
   * subsidence mode (driftCoords unused) but keeping it cheap and unconditional
   * means the layer never reads stale data after a mode toggle.
   * ------------------------------------------------------------------------ */
  const [driftVersion, setDriftVersion] = useState(0);
  useEffect(() => {
    if (!bundle || !staticBinary) return;
    const { n_polys, startIndices, driftCoords } = staticBinary;
    const src = bundle.ringCoords;
    if (mode === "subsidence") {
      driftCoords.set(src);
      setDriftVersion(v => v + 1);
      return;
    }
    const m = monthIdx;
    for (let i = 0; i < n_polys; i++) {
      const start = startIndices[i];
      const end   = startIndices[i + 1];
      const fused = bundle.fusedHeightM[i];
      const h_m   = Number.isFinite(fused) && fused > 0 ? fused : (bundle.heightM[i] || 6);
      const ew    = horizontalVelocityAt(bundle, i, m);
      // Offset in meters; convert to longitude degrees at this building's latitude.
      const offset_m = h_m * ew * DRIFT_VISUAL_GAIN_M_PER_MM_PER_M;
      // Use the first vertex's latitude as the reference (footprint is small).
      const lat0 = src[start * 2 + 1];
      const cosLat = Math.cos(lat0 * Math.PI / 180);
      const dLon = offset_m / (111_320.0 * (cosLat || 1));
      for (let v = start; v < end; v++) {
        driftCoords[v * 2]     = src[v * 2] + dLon;
        driftCoords[v * 2 + 1] = src[v * 2 + 1];
      }
    }
    setDriftVersion(v => v + 1);
  }, [bundle, staticBinary, monthIdx, mode]);

  /* ---- deck.gl layer --------------------------------------------------- */
  const layers = useMemo(() => {
    if (!bundle || !staticBinary) return [];
    const { n_polys, startIndices, elevation, fillColor, driftCoords } = staticBinary;

    const ref = bundle.header.aoi.reference;
    const refPt = ref && Number.isFinite(ref.lon) && Number.isFinite(ref.lat)
      ? [{ position: [ref.lon, ref.lat] as [number, number], note: ref.note }]
      : [];

    return [
      // C3 — block aggregation overlay (toggleable). Drawn under the 3D
      // buildings as a flat choropleth colored by the block's worst velocity.
      ...(showBlocks ? [
        new PolygonLayer({
          id: "block-overlay",
          data: blockData,
          getPolygon: (d: { polygon: [number, number][] }) => d.polygon,
          getFillColor: (d: { worstVelocity: number }) => {
            const [r, g, b] = velocityToRGBA(d.worstVelocity, 1);
            return [r, g, b, 70] as [number, number, number, number];
          },
          getLineColor: [148, 163, 184, 140],
          getLineWidth: 1,
          lineWidthUnits: "pixels",
          stroked: true,
          filled: true,
          extruded: false,
          pickable: true,
          parameters: { depthTest: false },
          updateTriggers: { getFillColor: [blockData] },
        } as never),
      ] : []),

      // WATCH cohort overlay (toggleable): amber outline around every
      // MIXED_SIGNAL building so the "trustworthy-but-non-linear movers" cohort
      // is visible at a glance, independent of the active velocity/risk ramp.
      // Amber (never red) — it reads as "watch", not "confirmed threat".
      // Non-pickable so clicks fall through to the building underneath.
      ...(showWatch ? [
        new PolygonLayer({
          id: "watch-overlay",
          data: watchData.polygons,
          getPolygon: (d: { polygon: [number, number][] }) => d.polygon,
          stroked: true,
          filled: false,
          extruded: false,
          getLineColor: [251, 191, 36, 220],   // amber-400, matches the WATCH badge
          getLineWidth: 1.5,
          lineWidthUnits: "pixels",
          pickable: false,
          parameters: { depthTest: false },
          updateTriggers: { getPolygon: [watchData] },
        } as never),
      ] : []),

      new SolidPolygonLayer({
        id: "buildings-3d",
        data: {
          length: n_polys,
          startIndices,
          attributes: {
            // driftCoords mirrors bundle.ringCoords in subsidence mode and
            // carries per-building east-west offsets in drift mode. Pointer
            // is stable across mode toggles; the version trigger forces a
            // GPU re-upload when contents change.
            getPolygon:   { value: driftCoords, size: 2 },
            getElevation: { value: elevation,   size: 1 },
            // normalized: true maps the uint8 0..255 values to 0..1 in the
            // shader (deck.gl's fill-color shader expects colors in 0..1).
            // With normalized:false the raw 0..255 ints clamp to 1.0 → every
            // building renders white regardless of its ramp color.
            getFillColor: { value: fillColor, size: 4, normalized: true },
          },
        },
        _normalize: false,           // we hand it well-formed rings; skip O(n) check
        extruded: true,
        wireframe: false,
        pickable: true,
        material: { ambient: 0.5, diffuse: 0.6, shininess: 12 },

        // updateTriggers refreshes the GPU attribute when the buffer content
        // changes (even though the array reference is stable).
        updateTriggers: {
          getFillColor: colorVersion,
          getPolygon:   driftVersion,
        },

        onClick: (info: { index?: number }) => {
          if (typeof info.index === "number" && info.index >= 0) {
            setSelectedRow(info.index);
          }
        },
        autoHighlight: false,
        parameters: { depthTest: true, depthMask: true },
        getLineColor: [0, 0, 0, 0],
      } as never),

      // Selected-building highlight is NOT a separate layer: the clicked
      // building's own vertices are recoloured to the animated glow in the
      // per-vertex fill buffer (see the repaint effect). No overlay object.

      // B4 — InSAR reference point (⚓). Stable anchor every velocity is
      // measured against. A ring + a glyph; tooltip carries the note.
      new ScatterplotLayer({
        id: "reference-ring",
        data: refPt,
        getPosition: (d: { position: [number, number] }) => d.position,
        getRadius: 9,
        radiusUnits: "pixels",
        stroked: true,
        filled: false,
        getLineColor: [56, 189, 248, 255],   // sky-400
        getLineWidth: 2,
        lineWidthUnits: "pixels",
        pickable: true,
        parameters: { depthTest: false },
      } as never),
      new TextLayer({
        id: "reference-label",
        data: refPt,
        getPosition: (d: { position: [number, number] }) => d.position,
        getText: () => "⚓",
        getSize: 16,
        sizeUnits: "pixels",
        getColor: [56, 189, 248, 255],
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
        parameters: { depthTest: false },
        pickable: true,
      } as never),

      // §8.1a — shop pins. A filled dot on each shop's footprint centroid; amber (off the data
      // ramp, so it never reads as risk). Clicking a pin selects its building, driving the
      // EXISTING glow + sidebar (onClick sets selectedRow — no new highlight path). Empty when
      // there are no shops, so a zero-shop AOI is byte-identical to the map without this layer.
      new ScatterplotLayer({
        id: "shop-pins",
        data: shopPins,
        getPosition: (d: ShopPin) => d.position,
        getRadius: 6,
        radiusUnits: "pixels",
        radiusMinPixels: 4,
        stroked: true,
        filled: true,
        getFillColor: (d: ShopPin) => {
          const [r, g, b] = d.shop.confirmed ? SHOP_PIN_CONFIRMED : SHOP_PIN;
          return [r, g, b, 230] as [number, number, number, number];
        },
        getLineColor: [12, 15, 20, 255],       // dark hairline so the dot reads over any fill
        getLineWidth: 1,
        lineWidthUnits: "pixels",
        pickable: true,
        parameters: { depthTest: false },
        onClick: (info: { object?: ShopPin }) => {
          if (info.object) openShopPin(info.object);
        },
        updateTriggers: { getFillColor: [shopPins] },
      } as never),
      // Shield glyph over CONFIRMED shops only — the ground-assessed-provenance mark (NOT a
      // safety claim). Non-confirmed shops show just the dot. Non-pickable: the dot underneath
      // owns the click/tooltip so the glyph never swallows a pin interaction.
      new TextLayer({
        id: "shop-confirmed-shield",
        data: shopPins.filter(p => p.shop.confirmed),
        getPosition: (d: ShopPin) => d.position,
        getText: () => "🛡",
        getSize: 13,
        sizeUnits: "pixels",
        getColor: [SHOP_PIN_CONFIRMED[0], SHOP_PIN_CONFIRMED[1], SHOP_PIN_CONFIRMED[2], 255] as [number, number, number, number],
        getPixelOffset: [0, -11],              // sit just above the dot
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
        parameters: { depthTest: false },
        pickable: false,
        updateTriggers: { getPosition: [shopPins] },
      } as never),
    ];
    // The selection glow lives in the fill buffer (colorVersion), so no
    // selection-specific dep is needed here.
  }, [bundle, staticBinary, colorVersion, driftVersion, showBlocks, blockData, showWatch, watchData, shopPins, openShopPin]);

  useEffect(() => {
    overlayRef.current?.setProps({
      layers,
      // Tooltip for the block overlay + reference pin. Building picks are
      // handled by onClick → sidebar, so we only surface the non-building
      // layers here.
      getTooltip: (info: { layer?: { id?: string } | null; object?: unknown }) => {
        const id = info.layer?.id;
        const o = info.object as Record<string, number> | { note?: string } | undefined;
        if (!o) return null;
        if (id === "block-overlay") {
          const d = o as Record<string, number>;
          return {
            text:
              `block · ${d.count} buildings\n` +
              `worst velocity: ${d.worstVelocity.toFixed(1)} mm/yr\n` +
              `mean risk: ${(d.meanRisk * 100).toFixed(0)} · max ${(d.maxRisk * 100).toFixed(0)}\n` +
              `confirmed threats: ${d.confirmed}`,
          };
        }
        if (id === "reference-ring" || id === "reference-label") {
          const note = (o as { note?: string }).note;
          return note ? { text: `⚓ reference\n${note}` } : null;
        }
        if (id === "shop-pins") {
          const { shop } = o as unknown as ShopPin;
          // Provenance line ONLY on a confirmed shop — "Confirmed" means a recorded
          // structural assessment on this footprint, never a safety endorsement.
          const line2 = shop.category ? `\n${shop.category}` : "";
          const line3 = shop.confirmed ? "\n🛡 Confirmed footprint" : "";
          return { text: `${shop.name}${line2}${line3}` };
        }
        return null;
      },
    });
  }, [layers]);

  /* ---- DEV-only e2e hook (§8.1a shops-on-map). Exposes deck's REAL render-buffer picking so a
   * live Playwright check can prove a shop pin actually PAINTED (not merely that data arrived) and
   * read its confirmed flag. Guarded by import.meta.env.DEV, so the whole block is tree-shaken out
   * of a production build — it can never reach real users. Never throws (a pick failure yields an
   * empty result). See PE/commerce/e2e/shops_on_map.fe.e2e.js. */
  useEffect(() => {
    if (!import.meta.env.DEV || typeof window === "undefined") return;
    const w = window as unknown as { __insarShopsE2E?: unknown };
    w.__insarShopsE2E = {
      // The shops the hook resolved onto footprints — what the layer WILL draw (data-plane truth).
      resolved: () => shopPins.map(p => ({
        building_id: p.shop.insar_building_id,
        name: p.shop.name,
        confirmed: p.shop.confirmed,
      })),
      partial: () => shopsPartial,
      // Render-plane truth: pick over the whole map canvas, filtered to the shop layers. A non-empty
      // result means deck actually rasterised a pin at those pixels (WebGL round-trip), the strongest
      // "it rendered" signal available for a canvas layer.
      pickPainted: () => {
        try {
          const canvas = mapRef.current?.getCanvas();
          if (!canvas) return [];
          const objs = overlayRef.current?.pickObjects({
            x: 0, y: 0,
            width: canvas.clientWidth || canvas.width,
            height: canvas.clientHeight || canvas.height,
            layerIds: ["shop-pins", "shop-confirmed-shield"],
          }) ?? [];
          return objs.map(o => {
            const sp = o.object as ShopPin | undefined;
            return sp ? { building_id: sp.shop.insar_building_id, confirmed: sp.shop.confirmed } : null;
          }).filter(Boolean);
        } catch {
          return [];
        }
      },
      // §8.1b pair-radiate: the bundle rows currently carrying a live fuchsia "connected" glow,
      // as building_ids (data-plane truth of what the fill buffer is overriding this frame). A
      // non-empty result after a pin-open (buyer) or a bus pulse (seller) proves the glow fired.
      contactGlowing: () => {
        const b = bundleRef.current;
        if (!b) return [] as number[];
        return Array.from(contactRef.current.keys())
          .filter(row => row >= 0 && row < b.buildingId.length)
          .map(row => b.buildingId[row]);
      },
      // Drive the REAL buyer trigger from the e2e: resolve the shop pin for `buildingId` and invoke
      // the exact production `openShopPin` (POST /insar/contact + local glow) — no WebGL pixel-click
      // to hunt for. Returns true iff a pin for that building was found and opened. This exercises
      // the same code path a genuine tap does; it does NOT bypass any of it.
      openShop: (buildingId: number) => {
        const pin = shopPins.find(p => p.shop.insar_building_id === buildingId);
        if (!pin) return false;
        openShopPin(pin);
        return true;
      },
    };
    return () => { delete (window as unknown as { __insarShopsE2E?: unknown }).__insarShopsE2E; };
  }, [shopPins, shopsPartial, contactVersion, openShopPin]);

  // Force a resize whenever the map CONTAINER changes size OR after layers first
  // appear. MapLibre measures its container once at mount; if the container
  // changes (window resize, devtools, OR the responsive layout reflowing the
  // map between the desktop side-by-side row and the mobile stacked column at
  // the lg breakpoint), the GL viewport keeps its old size and 3D buildings
  // render outside the frustum / the map looks clipped. A ResizeObserver on the
  // container catches ALL of these — including container-only resizes that fire
  // no window 'resize' event (e.g. the flex reflow when the sidebar moves below
  // the map) — which the old window-only listener missed.
  useEffect(() => {
    if (!mapRef.current || !mapContainer.current) return;
    const m = mapRef.current;
    const r = () => m.resize();
    const ro = new ResizeObserver(r);
    ro.observe(mapContainer.current);
    window.addEventListener("resize", r);
    // Kick a resize a beat after layers exist so any late layout settles.
    const t = window.setTimeout(r, 100);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", r);
      window.clearTimeout(t);
    };
  }, [layers.length > 0]);

  /* ---- Playback -------------------------------------------------------- */
  useEffect(() => {
    if (!playing || !bundle) return;
    const id = window.setInterval(() => {
      const next = (monthIdxRef.current + 1) % bundle.header.n_months;
      monthIdxRef.current = next;
      setMonthIdxState(next);
    }, 320);
    return () => window.clearInterval(id);
  }, [playing, bundle]);

  const handleScrub = (m: number) => {
    monthIdxRef.current = m;
    setMonthIdxState(m);
  };

  /* ---- Zoom controls ---------------------------------------------------
   * Stable identities (mapRef is a ref → empty deps) so the memoized
   * ZoomControl never reconciles on the parent's per-frame playback renders.
   * MapLibre's zoomIn/zoomOut animate ±1 and clamp to the style's min/max. */
  const handleZoomIn  = useCallback(() => mapRef.current?.zoomIn(),  []);
  const handleZoomOut = useCallback(() => mapRef.current?.zoomOut(), []);

  return (
    <>
      {/* The map container is position:absolute, so this wrapper MUST be given a
          real height or the map renders 0×0 and the screen stays dark (verified:
          mapContainer {h:0} when the wrapper collapses). The wrapper's height
          source differs per layout:
          - lg+ (row): the root is a horizontal flex; flex-1 fills WIDTH and
            h-full pins HEIGHT to the full-height row.
          - <lg (column): the root is a vertical flex with no intrinsic height to
            distribute, so h-full would collapse. We give the map an EXPLICIT
            viewport-height share (60svh) and let the sidebar take the rest below
            it. svh (not vh) so mobile browser chrome doesn't clip it. */}
      <div className="relative w-full h-[60svh] shrink-0 lg:h-full lg:flex-1">
        {/* Inline style: maplibre-gl.css ships `.maplibregl-map { position: relative }`
            which loads AFTER Tailwind and beats `absolute` on specificity tie. The
            container then collapses to height 0 because nothing in flow gives it
            height. Inline style wins over any stylesheet rule, full stop. */}
        <div
          ref={mapContainer}
          style={{ position: "absolute", inset: 0 }}
        />

        <TopBar
          aois={aois}
          activeCode={activeCode}
          onSelect={setActiveCode}
          observationDate={bundle?.header.dates[monthIdx]}
          mode={mode}
          onModeChange={onModeChange}
          idle={monthIdx === 0 && !playing}
          showBlocks={showBlocks}
          onToggleBlocks={setShowBlocks}
          showWatch={showWatch}
          onToggleWatch={setShowWatch}
          watchCount={watchData.order.length}
        />

        <TimeSlider
          n={bundle?.header.n_months ?? 0}
          idx={monthIdx}
          onScrub={handleScrub}
          playing={playing}
          onTogglePlay={() => setPlaying(p => !p)}
          dates={bundle?.header.dates}
        />

        <ZoomControl
          onZoomIn={handleZoomIn}
          onZoomOut={handleZoomOut}
          canZoomIn={canZoomIn}
          canZoomOut={canZoomOut}
        />

        {!bundle && (
          <div className="absolute inset-0 grid place-items-center pointer-events-none">
            <div className="text-xs uppercase tracking-[0.3em] text-slate-500 animate-pulse">
              Loading bundle…
            </div>
          </div>
        )}

        {/* §8.1a — honest degrade hint: shown only when the shops read failed, so a user
            doesn't read "no shop pins" as "no shops here". The subsidence map itself is
            unaffected; this is a subtle corner note, never a blocking error. */}
        {shopsPartial && (
          <div className="absolute bottom-4 left-4 pointer-events-none">
            <div className="text-[10px] uppercase tracking-[0.2em] text-amber-300/80 bg-black/40 backdrop-blur-sm rounded px-2 py-1 border border-amber-300/20">
              Shops unavailable
            </div>
          </div>
        )}
      </div>

      <ThreatSidebar
        bundle={bundle}
        selectedRow={selectedRow}
        monthIdx={monthIdx}
        activeAoi={activeAoi}
        mode={mode}
        idle={monthIdx === 0 && !playing}
        watchOrder={watchData.order}
        onFocusBuilding={focusBuilding}
        deepLinkFocusNonce={deepLinkFocusNonce}
      />
    </>
  );
}


/* ===========================================================================
 *  Color ramp
 *  velocity_mm_yr in [-25, +5] → red → amber → green → cyan
 *  Low coherence: desaturated.
 *  Selection no longer touches fill — the clicked building keeps its true color
 *  and is marked by the animated cyan/white halo instead.
 * =========================================================================== */
function velocityToRGBA(v: number, coh: number): [number, number, number, number] {
  // Clamp + normalize
  let t = (v - VELOCITY_FLOOR_MM_YR) / (VELOCITY_CEIL_MM_YR - VELOCITY_FLOOR_MM_YR);
  if (t < 0) t = 0; else if (t > 1) t = 1;

  // 3-stop ramp:  red (t=0) → amber (t≈0.55) → green (t=1)
  let r: number, g: number, b: number;
  if (t < 0.55) {
    const u = t / 0.55;
    r = 239 + (245 - 239) * u;
    g = 68  + (158 - 68)  * u;
    b = 68  + (11  - 68)  * u;
  } else {
    const u = (t - 0.55) / 0.45;
    r = 245 + (34  - 245) * u;
    g = 158 + (197 - 158) * u;
    b = 11  + (94  - 11)  * u;
  }

  // Desaturate when InSAR coherence is low — "we don't trust this number".
  if (coh < 0.30) {
    const gray = 0.299 * r + 0.587 * g + 0.114 * b;
    const mix = (coh / 0.30); // 0 at low end, 1 at threshold
    r = gray + (r - gray) * mix;
    g = gray + (g - gray) * mix;
    b = gray + (b - gray) * mix;
  }

  return [Math.round(r), Math.round(g), Math.round(b), 220];
}


/* ===========================================================================
 *  Idle risk-rank ramp
 *  rank ∈ [0,1] = composite-risk percentile WITHIN the AOI (1 = worst).
 *      green (rank=0, safest) → amber (rank≈0.5) → red (rank=1, highest risk).
 *  Note the direction is the INTUITIVE one (high risk = red) and therefore
 *  OPPOSITE to velocityToRGBA, whose t is keyed on signed velocity. Same
 *  green/amber/red stops, traversed the other way, plus the shared coherence
 *  desaturation so untrustworthy footprints never shout a confident colour.
 * =========================================================================== */
function riskRankToRGBA(rank: number, coh: number): [number, number, number, number] {
  let t = rank;
  if (t < 0) t = 0; else if (t > 1) t = 1;

  // Stops: green (t=0) → amber (t≈0.5) → red (t=1).
  let r: number, g: number, b: number;
  if (t < 0.5) {
    const u = t / 0.5;                 // green → amber
    r = 34  + (245 - 34)  * u;
    g = 197 + (158 - 197) * u;
    b = 94  + (11  - 94)  * u;
  } else {
    const u = (t - 0.5) / 0.5;         // amber → red
    r = 245 + (239 - 245) * u;
    g = 158 + (68  - 158) * u;
    b = 11  + (68  - 11)  * u;
  }

  // Desaturate when InSAR coherence is low — "we don't trust this footprint".
  if (coh < 0.30) {
    const gray = 0.299 * r + 0.587 * g + 0.114 * b;
    const mix = coh / 0.30;
    r = gray + (r - gray) * mix;
    g = gray + (g - gray) * mix;
    b = gray + (b - gray) * mix;
  }

  return [Math.round(r), Math.round(g), Math.round(b), 220];
}


/* ===========================================================================
 *  Drift color ramp
 *  ew_velocity_mm_yr in [-EW_VELOCITY_BOUND, +EW_VELOCITY_BOUND]:
 *      strong west = blue, zero = neutral grey, strong east = orange.
 *  Low coherence: desaturated (same as subsidence ramp).
 *  Selection no longer touches fill (see velocityToRGBA).
 * =========================================================================== */
function driftToRGBA(ew: number, coh: number): [number, number, number, number] {
  // Drift not measured (1-look / ASC-only building: no descending pass to
  // decompose east-west). Paint a flat, desaturated slate — honestly "unknown",
  // not a blue/orange drift reading we don't have. NaN must never reach the ramp
  // math below (it propagates to Math.round(NaN) and paints garbage).
  if (!Number.isFinite(ew)) return [71, 85, 105, 90];   // slate-600 @ low alpha
  // Normalize ew to t in [0, 1] where 0 = -bound (west) and 1 = +bound (east).
  let t = (ew + EW_VELOCITY_BOUND) / (2 * EW_VELOCITY_BOUND);
  if (t < 0) t = 0; else if (t > 1) t = 1;

  // 3-stop diverging ramp: blue (west) → grey (zero) → orange (east).
  // Anchor colors:  #38bdf8 (sky-400) → #64748b (slate-500) → #f97316 (orange-500)
  let r: number, g: number, b: number;
  if (t < 0.5) {
    const u = t / 0.5;
    r =  56 + (100 -  56) * u;
    g = 189 + (116 - 189) * u;
    b = 248 + (139 - 248) * u;
  } else {
    const u = (t - 0.5) / 0.5;
    r = 100 + (249 - 100) * u;
    g = 116 + (115 - 116) * u;
    b = 139 + ( 22 - 139) * u;
  }

  if (coh < 0.30) {
    const gray = 0.299 * r + 0.587 * g + 0.114 * b;
    const mix = coh / 0.30;
    r = gray + (r - gray) * mix;
    g = gray + (g - gray) * mix;
    b = gray + (b - gray) * mix;
  }

  return [Math.round(r), Math.round(g), Math.round(b), 220];
}


/* ===========================================================================
 *  MapLibre style — falls back to a flat dark canvas if no PMTiles file is
 *  available. As soon as `frontend/public/tiles/nairobi.pmtiles` exists, the
 *  pmtiles:// source will resolve and overlay road/building base layers.
 * =========================================================================== */
function emptyDarkStyle(): maplibregl.StyleSpecification {
  return {
    version: 8,
    sources: {},
    layers: [{ id: "bg", type: "background", paint: { "background-color": "#070a0f" } }],
  } as unknown as maplibregl.StyleSpecification;
}

/**
 * Dark basemap built against the Protomaps schema. Source layers used:
 *   water, landuse, roads, buildings, boundaries, places
 * Activated only after the probe in the init effect sees the .pmtiles file.
 */
function protomapsDarkStyle(): maplibregl.StyleSpecification {
  return {
    version: 8,
    // Self-hosted Noto Sans glyphs (fetch_glyphs.sh). A symbol layer with a
    // text-field renders nothing without this; served as a local static asset
    // so labels work offline, matching the local-first pmtiles:// basemap.
    glyphs: "/fonts/{fontstack}/{range}.pbf",
    sources: {
      basemap: {
        type: "vector",
        url: "pmtiles:///tiles/nairobi.pmtiles",
        attribution: "© OpenStreetMap · Protomaps",
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#070a0f" } },
      {
        id: "water",
        type: "fill",
        source: "basemap",
        "source-layer": "water",
        paint: { "fill-color": "#0a1726" },
      },
      {
        id: "landuse",
        type: "fill",
        source: "basemap",
        "source-layer": "landuse",
        paint: { "fill-color": "#0e141c", "fill-opacity": 0.6 },
      },
      {
        id: "roads-casing",
        type: "line",
        source: "basemap",
        "source-layer": "roads",
        minzoom: 12,
        paint: {
          "line-color": "#11161e",
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.5, 16, 4],
        },
      },
      {
        id: "roads",
        type: "line",
        source: "basemap",
        "source-layer": "roads",
        paint: {
          "line-color": "#3a4756",
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.3, 16, 1.6],
        },
      },
      {
        id: "buildings-base",
        type: "fill",
        source: "basemap",
        "source-layer": "buildings",
        minzoom: 13,
        paint: { "fill-color": "#1a2230", "fill-opacity": 0.25 },
      },
      {
        id: "boundaries",
        type: "line",
        source: "basemap",
        "source-layer": "boundaries",
        paint: { "line-color": "#2a3445", "line-dasharray": [2, 2], "line-width": 0.6 },
      },
      // --- Labels (drawn last, over every fill/line). The roads/places layers
      // carry `name` + `name:en`; we prefer the romanized name and fall back to
      // local. Collision handling is the MapLibre default (allow-overlap:false),
      // so labels de-clutter as you pan/zoom. text-color/halo match the dark bg.
      {
        id: "roads-labels",
        type: "symbol",
        source: "basemap",
        "source-layer": "roads",
        minzoom: 13,
        filter: ["has", "name"],
        layout: {
          "symbol-placement": "line",
          "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
          "text-font": ["Noto Sans Regular"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 13, 10, 18, 15],
          "text-max-angle": 30,
          "symbol-spacing": 250,
        },
        paint: {
          "text-color": "#9fb0c3",
          "text-halo-color": "#070a0f",
          "text-halo-width": 1.2,
        },
      },
      {
        id: "places-labels",
        type: "symbol",
        source: "basemap",
        "source-layer": "places",
        minzoom: 11,
        filter: ["has", "name"],
        layout: {
          "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
          "text-font": ["Noto Sans Regular"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 11, 11, 16, 16],
          "text-anchor": "center",
        },
        paint: {
          "text-color": "#c3d0e0",
          "text-halo-color": "#070a0f",
          "text-halo-width": 1.4,
        },
      },
    ],
  } as unknown as maplibregl.StyleSpecification;
}


/**
 * Per-error-kind copy. Each branch tells the user exactly what to check next:
 *   - network: API process is the suspect (uvicorn down, wrong port).
 *   - http:    API process answered but rejected the call (4xx/5xx body).
 *   - parse:   wire format mismatch; almost always a stale browser cache
 *              holding a pre-fix bundle. Hard-refresh (Ctrl-Shift-R) clears it.
 */
function ErrorPanel({ err, where }: { err: ApiError; where: string }) {
  let title: string;
  let hint: string;
  switch (err.kind) {
    case "network":
      title = `API unreachable — could not connect to ${where}`;
      hint = "Is uvicorn running on :8000?  Check with `curl -s localhost:8000/health` or `pgrep -af uvicorn`.";
      break;
    case "http":
      title = `API error ${err.status} from ${where}`;
      hint = err.status >= 500
        ? "The backend is up but threw an error. Check the uvicorn terminal for the traceback."
        : "The request was rejected. Verify the AOI code or query parameters.";
      break;
    case "parse":
      title = `Failed to decode ${where}`;
      hint = "Stale browser cache is the usual cause for the Int32Array alignment error. Hard-refresh (Ctrl-Shift-R) to drop the cached bundle, or clear site data.";
      break;
  }
  return (
    <div className="h-screen w-screen grid place-items-center bg-ink-950 text-slate-200 font-mono">
      <div className="max-w-md border border-red-500/40 bg-red-950/30 p-6">
        <div className="text-xs uppercase tracking-widest text-red-400">Error</div>
        <div className="mt-2 text-sm">{title}</div>
        <div className="mt-1 text-xs text-slate-500 break-words">{err.message}</div>
        <div className="mt-3 text-xs text-slate-400">{hint}</div>
      </div>
    </div>
  );
}

/* re-export so consumers can read inline (e.g. risk panel) */
export { velocityAt, displacementAt, coherenceAt, horizontalVelocityAt, buildingSeries };
