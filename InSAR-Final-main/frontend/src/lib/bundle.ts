/**
 * AOI bundle loader.
 *
 * Wire format (see backend/app/main.py:_build_bundle):
 *   [u32 header_len LE][header_len bytes UTF-8 JSON][packed binary arrays]
 *
 * The JSON header carries offsets/lengths/dtype for each array in the binary
 * section. We expose typed-array views into the original ArrayBuffer — zero
 * copies anywhere on the hot path.
 *
 * Building-id → row-index lookup is precomputed into a Map for O(1) selection.
 */

import { authHeaders } from "./telemetry";

export type ArrayDtype = "i4" | "i2" | "u1" | "u2" | "f4";

/** ARCHITECTURE_THREE B4 — InSAR reference point (the ⚓ pin). Every velocity
 *  in the bundle is measured relative to this lat/lon. */
export interface AoiReference {
  lon: number;
  lat: number;
  note: string;
}

/** ARCHITECTURE_THREE C1/C3 — fixed-grid block descriptor. The dense per-block
 *  aggregate arrays index [0, n_blocks); block id b maps to grid cell
 *  (ix = b % nx, iy = b / nx) whose SW corner is
 *  (minlon + ix*cellLonDeg, minlat + iy*cellLatDeg). */
export interface BlockGrid {
  nx: number;
  ny: number;
  n_blocks: number;
  minlon: number;
  minlat: number;
  cell_lon_deg: number;
  cell_lat_deg: number;
}

/** Three-state realness ladder (see backend/scripts/provenance.py). Drives the
 *  sidebar disclaimer copy.
 *  - 'synthetic' — everything fabricated.
 *  - 'partial'   — real footprints + real terrain (SoilGrids soil, OSM
 *                  riparian/shoreline), synthetic velocity until the SBAS run.
 *  - 'insar'     — real MintPy join; velocity is real too. */
export type DataProvenance = "synthetic" | "partial" | "insar";

export interface BundleHeader {
  aoi: {
    code: string;
    name: string;
    center_lon: number;
    center_lat: number;
    side_m: number;
    phenomenon: string;
    footprint_source: string;
    narrative: string;
    bbox: [number, number, number, number];
    reference: AoiReference;
  };
  n_buildings: number;
  n_months: number;
  /** Per-AOI defensibility gate. σ ≤ sigma_max = trustworthy velocity (the gate
   *  the backend classifier uses); the ConfidencePill tints against it. null when
   *  the AOI has no finite σ (empty stack). */
  sigma_max: number | null;
  r2_min: number;              // linear-fit floor (parity with backend gate)
  n_coh_epochs: number;        // B2 — coh_series reshapes to (n_buildings, n_coh_epochs)
  dates: string[];
  soil_classes: string[];
  block_grid: BlockGrid;       // C1/C3
  data_provenance: DataProvenance;
  arrays: {
    name: string;
    dtype: ArrayDtype;
    byteOrder: "<";
    shape: number[];
    offset: number;
    length: number;
  }[];
}

/**
 * Coherence-velocity classification, packed as uint8 per building.
 * Mirrors backend `_classify()` in scripts/phenomena.py.
 */
export const Classification = {
  INDETERMINATE:   0,
  CONFIRMED_THREAT: 1,
  ENV_NOISE:       2,
  STABLE_ANCHOR:   3,
  // V4-gap row: real signal at borderline coherence, or moderate signal at
  // high coherence — "something's moving but we can't confidently name it."
  // This is the WATCH cohort: trustworthy-but-non-linear (accelerating/curving)
  // movers. NOT damped on the backend (the old ×0.7 was removed); ranked on its
  // true composite_risk so it surfaces, not buried.
  MIXED_SIGNAL:    4,
  // Court-defensibility gate: the displacement time series is not a defensible
  // linear trend (low R²/high σ), so we refuse a confident safety verdict.
  // Composite dampened to 50% on the backend; kept visible in rankings.
  INSUFFICIENT_EVIDENCE: 5,
} as const;
export type ClassificationCode = (typeof Classification)[keyof typeof Classification];

/**
 * STL-decomposed failure mode, packed as uint8 per building.
 * Mirrors backend `_stl_decompose()` thresholds.
 */
export const FailureMode = {
  ELASTIC: 0,   // seasonal soil response — breathes with the rain
  PLASTIC: 1,   // progressive trend — foundation actually failing
} as const;
export type FailureModeCode = (typeof FailureMode)[keyof typeof FailureMode];

// Absolute danger tier (postprocess.danger_level). Comparable across AOIs by
// construction (fixed mm/yr cutoffs, not percentile). Single source of truth for
// the threat badge — the tier picks the headline + colour; movement details only
// refine the wording within a tier.
export const DangerLevel = {
  STABLE: 0,
  LOW: 1,
  ELEVATED: 2,
  HIGH: 3,
  CRITICAL: 4,
} as const;
export type DangerLevelCode = (typeof DangerLevel)[keyof typeof DangerLevel];

export interface Bundle {
  header: BundleHeader;
  /** Index in flat arrays for a given building_id. O(1) */
  byBuildingId: Map<number, number>;

  // -- typed-array views (zero-copy) -----------------------------------------
  buildingId: Int32Array;
  heightM: Float32Array;              // floor-count estimate (n_floors × ~3 m)
  insarHeightM: Float32Array;         // InSAR phase-fringe inversion
  insarHeightSigmaM: Float32Array;    // per-building σ on the InSAR estimate
  fusedHeightM: Float32Array;         // inverse-variance blend; used for 3D extrusion
  heightImputed: Uint8Array;          // 1 = source had no height; value is estimated, not measured
  insarPixelShare: Uint16Array;       // # buildings sharing this building's ~78 m InSAR cell (≥1)
  nFloors: Int16Array;
  riparianDistM: Float32Array;
  shorelineDistM: Float32Array;
  reclaimedLand: Uint8Array;
  classification: Uint8Array;         // ClassificationCode per building
  // External structural-flag state: 0=NONE 1=CLEARED 2=UNSAFE 3=AUTH_UNSAFE. Non-zero
  // = a certifier recorded an on-the-ground assessment (ground-verified provenance,
  // NOT a safety verdict) — drives the "Confirmed" shield in the sidebar.
  structuralFlagState: Uint8Array;
  velocityAccelMmYr2: Float32Array;   // annualized 6-mo acceleration; - = accelerating subs
  // STL trend decoupling (Tier 2 in ARCHITECTURE_TWO)
  trendSlopeMmYr:       Float32Array; // annualized slope of STL trend component
  seasonalAmplitudeMm:  Float32Array; // peak-to-peak of STL seasonal component
  trendR2:              Float32Array; // 1 - var(resid)/var(disp); fit quality
  failureMode:          Uint8Array;   // FailureModeCode per building (0=ELASTIC 1=PLASTIC)
  dangerLevel:          Uint8Array;   // DangerLevelCode per building (0=STABLE … 4=CRITICAL); cross-AOI absolute tier
  // Tier 3 (honesty layer): σ on the velocity estimates and peer-cohort percentiles.
  velocitySigmaMmYr:    Float32Array; // σ ≈ k*(1-γ); paired with velocityMmYr end-of-series
  velocityEwSigmaMmYr:  Float32Array; // σ on horizontal-drift; same coherence-driven model
  cohortCompositePct:   Uint8Array;   // 0-100 percentile rank of composite_risk in peer cohort
  cohortShearPct:       Uint8Array;   // 0-100 percentile rank of |v_ew| in peer cohort
  cohortSize:           Uint16Array;  // # of buildings in this building's peer cohort
  // ARCHITECTURE_THREE C1/C4 — per-building block membership + block-relative cohort
  blockId:              Uint16Array;  // fixed-grid block id (iy*nx + ix); index into block aggregates
  cohortBlockPct:       Uint8Array;   // 0-100 percentile rank of composite_risk within this building's block
  ringCoords: Float32Array;     // flat [lon,lat,lon,lat,...]
  ringOffsets: Int32Array;      // [n_buildings+1]; ring start = offsets[i] / 2, end = offsets[i+1] / 2 (in vertex units)

  /** Row-major [n_buildings * n_months] */
  displacementMm:        Float32Array;
  /** Row-major [n_buildings * n_months] — STL-decoupled trend; rain-decoupled signal */
  trendDisplacementMm:   Float32Array;
  velocityMmYr:          Float32Array;
  velocityHorizontalEw:  Float32Array;     // + east, - west
  coherence:             Float32Array;
  /** B2 — packed [n_buildings * n_coh_epochs] coherence sparkline */
  cohSeries:             Float32Array;

  // Continuous [0,1] COLLAPSE SCORE: movement-dominant, susceptibility amplifies
  // only (a still building scores ~0 regardless of soil). Drives the within-AOI
  // heat-map ranking. For the absolute, cross-AOI tier use `dangerLevel`.
  compositeRisk:  Float32Array;

  // ARCHITECTURE_THREE C1 — dense per-block aggregates, indexed [0, n_blocks).
  // Empty blocks have count 0 and zeroed metrics.
  blockCount:           Int32Array;    // buildings in block
  blockWorstVelocity:   Float32Array;  // most-negative end velocity (mm/yr); 0 if empty
  blockMeanRisk:        Float32Array;  // mean composite_risk; 0 if empty
  blockMaxRisk:         Float32Array;  // max composite_risk; 0 if empty
  blockConfirmed:       Int32Array;    // # CONFIRMED_THREAT buildings in block
}


/** Per-dtype byte alignment that the browser will enforce. */
const ALIGN: Record<ArrayDtype, number> = { f4: 4, i4: 4, i2: 2, u2: 2, u1: 1 };

/**
 * Build a typed-array view. Pre-checks the alignment so that when the
 * server-side packing regresses, the thrown error names exactly which array,
 * dtype, offset, and required alignment — not the bare browser message
 * "start offset of Int32Array should be a multiple of 4".
 */
function viewFor(
  buf: ArrayBuffer,
  meta: BundleHeader["arrays"][number],
  bufByteLength: number,
): ArrayBufferView {
  const count = meta.shape.reduce((a, b) => a * b, 1);
  const need = ALIGN[meta.dtype];
  if (need == null) throw new Error(`unsupported dtype: ${meta.dtype}`);
  if (meta.offset % need !== 0) {
    throw new Error(
      `array '${meta.name}' (${meta.dtype}) offset=${meta.offset} not aligned to ${need}`
      + ` — buf.byteLength=${bufByteLength}, shape=[${meta.shape.join(",")}], length=${meta.length}`,
    );
  }
  if (meta.offset + count * need > bufByteLength) {
    throw new Error(
      `array '${meta.name}' (${meta.dtype}) extends past buffer:`
      + ` offset=${meta.offset} count=${count} need=${count * need} bytes,`
      + ` buf.byteLength=${bufByteLength}`,
    );
  }
  switch (meta.dtype) {
    case "f4": return new Float32Array(buf, meta.offset, count);
    case "i4": return new Int32Array(buf, meta.offset, count);
    case "i2": return new Int16Array(buf, meta.offset, count);
    case "u2": return new Uint16Array(buf, meta.offset, count);
    case "u1": return new Uint8Array(buf, meta.offset, count);
  }
}


export async function fetchBundle(aoiCode: string, signal?: AbortSignal): Promise<Bundle> {
  // cache: "no-store" guarantees the browser does NOT replay a stale entry
  // from disk cache (e.g. one written by an earlier `Cache-Control: immutable`
  // response). Combined with the server's ETag + no-cache, this means the
  // browser always sees the current bundle bytes — no hard-refresh needed.
  const res = await fetch(`/api/aoi/${aoiCode}/bundle`, {
    signal,
    cache: "no-store",
    headers: authHeaders(), // RS256 telemetry token — the data API is auth-gated
  });
  if (!res.ok) throw new Error(`bundle fetch failed: ${res.status}`);
  const buf = await res.arrayBuffer();
  return parseBundle(buf);
}


export function parseBundle(buf: ArrayBuffer): Bundle {
  if (buf.byteLength < 4) {
    throw new Error(`bundle too short: byteLength=${buf.byteLength}`);
  }
  const headerLen = new DataView(buf, 0, 4).getUint32(0, /*littleEndian=*/true);
  if (headerLen <= 0 || headerLen > buf.byteLength - 4) {
    throw new Error(`bundle header length implausible: headerLen=${headerLen}, buf.byteLength=${buf.byteLength}`);
  }
  const headerBytes = new Uint8Array(buf, 4, headerLen);
  let header: BundleHeader;
  try {
    header = JSON.parse(new TextDecoder().decode(headerBytes));
  } catch (e) {
    // Most common cause: a dev-server SPA fallback (`<!doctype html>...`)
    // got served where an /api/... bundle was expected.
    const sniff = Array.from(headerBytes.subarray(0, 16))
      .map(b => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : "."))
      .join("");
    throw new Error(
      `bundle header is not valid JSON (likely SPA fallback served as bundle).`
      + ` First 16 bytes: ${JSON.stringify(sniff)}. buf.byteLength=${buf.byteLength}. (${(e as Error).message})`,
    );
  }

  // The binary body starts after the 4-byte length prefix + the header bytes.
  // The numpy `offset` fields in the header are relative to the start of the
  // body, so we must shift them by (4 + headerLen) before slicing.
  const bodyStart = 4 + headerLen;
  const indexed: Record<string, ArrayBufferView> = {};
  for (const a of header.arrays) {
    indexed[a.name] = viewFor(buf, { ...a, offset: a.offset + bodyStart }, buf.byteLength);
  }

  const buildingId = indexed["building_id"] as Int32Array;
  const byBuildingId = new Map<number, number>();
  for (let i = 0; i < buildingId.length; i++) byBuildingId.set(buildingId[i], i);

  return {
    header,
    byBuildingId,

    buildingId,
    heightM:              indexed["height_m"]                  as Float32Array,
    insarHeightM:         indexed["insar_height_m"]            as Float32Array,
    insarHeightSigmaM:    indexed["insar_height_sigma_m"]      as Float32Array,
    fusedHeightM:         indexed["fused_height_m"]            as Float32Array,
    heightImputed:        indexed["height_imputed"]            as Uint8Array,
    insarPixelShare:      indexed["insar_pixel_share"]         as Uint16Array,
    nFloors:              indexed["n_floors"]                  as Int16Array,
    riparianDistM:        indexed["riparian_dist_m"]           as Float32Array,
    shorelineDistM:       indexed["shoreline_dist_m"]          as Float32Array,
    reclaimedLand:        indexed["reclaimed_land"]            as Uint8Array,
    classification:       indexed["classification"]            as Uint8Array,
    // Defensive: an older backend won't send this array — fall back to all-zero
    // (every building "unflagged") so a stale bundle can never crash the parser.
    structuralFlagState:  (indexed["structural_flag_state"]    as Uint8Array)
                          ?? new Uint8Array(buildingId.length),
    velocityAccelMmYr2:   indexed["velocity_accel_mm_yr2"]     as Float32Array,
    trendSlopeMmYr:       indexed["trend_slope_mm_yr"]         as Float32Array,
    seasonalAmplitudeMm:  indexed["seasonal_amplitude_mm"]     as Float32Array,
    trendR2:              indexed["trend_r2"]                  as Float32Array,
    failureMode:          indexed["failure_mode"]              as Uint8Array,
    dangerLevel:          indexed["danger_level"]              as Uint8Array,
    velocitySigmaMmYr:    indexed["velocity_sigma_mm_yr"]      as Float32Array,
    velocityEwSigmaMmYr:  indexed["velocity_ew_sigma_mm_yr"]   as Float32Array,
    cohortCompositePct:   indexed["cohort_composite_pct"]      as Uint8Array,
    cohortShearPct:       indexed["cohort_shear_pct"]          as Uint8Array,
    cohortSize:           indexed["cohort_size"]               as Uint16Array,
    blockId:              indexed["block_id"]                  as Uint16Array,
    cohortBlockPct:       indexed["cohort_block_pct"]          as Uint8Array,
    ringCoords:           indexed["ring_coords"]               as Float32Array,
    ringOffsets:          indexed["ring_offsets"]              as Int32Array,
    displacementMm:       indexed["displacement_mm"]           as Float32Array,
    trendDisplacementMm:  indexed["trend_displacement_mm"]     as Float32Array,
    velocityMmYr:         indexed["velocity_mm_yr"]            as Float32Array,
    velocityHorizontalEw: indexed["velocity_horizontal_ew"]    as Float32Array,
    coherence:            indexed["coherence"]                 as Float32Array,
    cohSeries:            indexed["coh_series"]                as Float32Array,
    compositeRisk:        indexed["composite_risk"]            as Float32Array,
    blockCount:           indexed["block_count"]               as Int32Array,
    blockWorstVelocity:   indexed["block_worst_velocity"]      as Float32Array,
    blockMeanRisk:        indexed["block_mean_risk"]           as Float32Array,
    blockMaxRisk:         indexed["block_max_risk"]            as Float32Array,
    blockConfirmed:       indexed["block_confirmed"]           as Int32Array,
  };
}


/* --------------------------------------------------------------------------
 * O(1) accessors. These are hot — keep them inlinable.
 * -------------------------------------------------------------------------- */

/** Velocity at month index `m` for building row `i`. O(1). */
export function velocityAt(b: Bundle, i: number, m: number): number {
  return b.velocityMmYr[i * b.header.n_months + m];
}

/**
 * Horizontal east-west velocity (mm/yr) at month index `m` for building `i`.
 * Sign convention: positive = eastward, negative = westward. Derived from
 * ascending/descending Sentinel-1 LOS pair via vector decomposition.
 */
export function horizontalVelocityAt(b: Bundle, i: number, m: number): number {
  return b.velocityHorizontalEw[i * b.header.n_months + m];
}

/** Displacement at month index `m` for building row `i`. O(1). */
export function displacementAt(b: Bundle, i: number, m: number): number {
  return b.displacementMm[i * b.header.n_months + m];
}

/** Coherence at month index `m` for building row `i`. O(1). */
export function coherenceAt(b: Bundle, i: number, m: number): number {
  return b.coherence[i * b.header.n_months + m];
}

/** Vertex count for building row `i`. O(1). */
export function vertexCount(b: Bundle, i: number): number {
  return (b.ringOffsets[i + 1] - b.ringOffsets[i]) / 2;
}

/**
 * Lon/lat centroid of building row `i`'s footprint ring — the arithmetic mean of its
 * vertices. Used to anchor a map marker (a shop pin) on a building without needing its raw
 * coordinates from anywhere else: the footprint is already in the bundle. O(k) in the ring's
 * vertex count (a small constant per building — footprints have a handful of vertices), and
 * the rings are always closed (first == last vertex), so the duplicate closing point biases
 * the mean negligibly and never changes which building it sits on. Returns null for a
 * degenerate (zero-vertex) ring so the caller can skip an un-anchorable marker.
 */
export function buildingCentroid(b: Bundle, i: number): [number, number] | null {
  const start = b.ringOffsets[i];       // in lon/lat scalar units (vertex*2)
  const end = b.ringOffsets[i + 1];
  if (end <= start) return null;
  let sumLon = 0;
  let sumLat = 0;
  let n = 0;
  for (let p = start; p < end; p += 2) {
    sumLon += b.ringCoords[p];
    sumLat += b.ringCoords[p + 1];
    n++;
  }
  if (n === 0) return null;
  return [sumLon / n, sumLat / n];
}

/**
 * Lon/lat polygon ring (closed) for block id `b`, as deck.gl-ready
 * [[lon,lat],...]. Derived from the header's block_grid descriptor. O(1).
 */
export function blockPolygon(b: Bundle, blockId: number): [number, number][] {
  const g = b.header.block_grid;
  const ix = blockId % g.nx;
  const iy = Math.floor(blockId / g.nx);
  const x0 = g.minlon + ix * g.cell_lon_deg;
  const y0 = g.minlat + iy * g.cell_lat_deg;
  const x1 = x0 + g.cell_lon_deg;
  const y1 = y0 + g.cell_lat_deg;
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
}

/** Per-building coherence sparkline subarray view (no copy). O(1). */
export function buildingCohSeries(b: Bundle, i: number): Float32Array {
  const k = b.header.n_coh_epochs;
  return b.cohSeries.subarray(i * k, (i + 1) * k);
}

/** Per-building time-series subarray view (no copy). O(1). */
export function buildingSeries(
  b: Bundle, i: number,
  which: "displacement" | "trend" | "velocity" | "coherence" | "horizontal_ew",
): Float32Array {
  const m = b.header.n_months;
  const src =
    which === "displacement"  ? b.displacementMm :
    which === "trend"         ? b.trendDisplacementMm :
    which === "velocity"      ? b.velocityMmYr :
    which === "horizontal_ew" ? b.velocityHorizontalEw :
                                b.coherence;
  return src.subarray(i * m, (i + 1) * m);
}

const _DANGER_LABEL = ["STABLE", "LOW", "ELEVATED", "HIGH", "CRITICAL"] as const;

/**
 * Serialize the current AOI's per-building screening columns to a CSV string —
 * client-side, O(n_buildings), from the already-in-memory bundle (no backend call,
 * so the locked read app stays untouched). Emits the danger TIER (not a bare %) plus
 * the data-provenance line, because this is a screening signal, NOT a structural-
 * safety verdict (risk_model.md / analysis_two.md §5): the disclaimer travels with
 * the data when it leaves the map.
 */
export function bundleToCsv(b: Bundle, aoiCode: string): string {
  const cols = [
    "building_id", "aoi", "danger_tier", "collapse_score",
    "velocity_mm_yr", "velocity_sigma_mm_yr", "ew_drift_mm_yr",
    "trend_r2", "coherence_end", "insar_pixel_share", "data_provenance",
  ];
  const prov = b.header.data_provenance;
  const m_last = b.header.n_months - 1;
  const lines: string[] = [
    "# Weespas/InSAR subsidence SCREENING export — NOT a structural-safety verdict; ground inspection required.",
    cols.join(","),
  ];
  for (let i = 0; i < b.buildingId.length; i++) {
    lines.push([
      b.buildingId[i],
      aoiCode,
      _DANGER_LABEL[b.dangerLevel[i]] ?? b.dangerLevel[i],
      b.compositeRisk[i].toFixed(4),
      velocityAt(b, i, m_last).toFixed(3),
      b.velocitySigmaMmYr[i].toFixed(3),
      horizontalVelocityAt(b, i, m_last).toFixed(3),
      b.trendR2[i].toFixed(3),
      coherenceAt(b, i, m_last).toFixed(3),
      b.insarPixelShare[i],
      prov,
    ].join(","));
  }
  return lines.join("\n");
}
