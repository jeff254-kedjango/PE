import { useEffect, useRef } from "react";
import { Bundle, Classification, DataProvenance, FailureMode, DangerLevel, velocityAt, displacementAt, coherenceAt, horizontalVelocityAt, buildingSeries, bundleToCsv } from "../lib/bundle";
import { AoiSummary } from "../lib/useAois";
import { meterExport } from "../lib/telemetry";
import { ViewMode } from "./TopBar";

// Movement thresholds — MIRROR the backend named constants in
// backend/scripts/postprocess.py so the sidebar's wording/styling never
// contradicts the danger_level badge it sits next to. On real Sentinel-1 InSAR
// the per-building acceleration noise floor is ≈8–10 mm/yr², so the old −3
// "accelerating" cutoff (tuned to the synthetic generator) fired on noise.
const ACCEL_HIGH_MM_YR2 = -8;   // DANGER_ACCEL_HIGH — accelerating subsidence (≈1σ)
const ACCEL_DECEL_MM_YR2 = 8;   // symmetric upper edge — decelerating
const VEL_SEVERE_MM_YR = -8;    // DANGER_VEL_ELEVATED — "severe" velocity tint starts here

export function ThreatSidebar({
  bundle, selectedRow, monthIdx, activeAoi, mode, idle,
  watchOrder, onFocusBuilding, deepLinkFocusNonce,
}: {
  bundle: Bundle | null;
  selectedRow: number | null;
  monthIdx: number;
  activeAoi: AoiSummary | null;
  mode: ViewMode;
  idle: boolean;
  watchOrder: number[];
  onFocusBuilding: (row: number) => void;
  deepLinkFocusNonce: number;
}) {
  // When the user arrives via Weespas "View Building Risk Analysis", scroll the divider
  // directly above the Structural Threat section to the top of the sidebar so that
  // building's analysis is immediately in view. Gated on the nonce (0 on ordinary visits),
  // so a plain map click or WATCH-stepper select never moves the scroll position.
  const threatAnchorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!deepLinkFocusNonce) return;
    threatAnchorRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [deepLinkFocusNonce]);

  if (!bundle || !activeAoi) return <SkeletonSidebar />;

  return (
    <aside className="w-full lg:w-[380px] flex-1 lg:flex-none min-h-0 shrink-0 border-t lg:border-t-0 lg:border-l border-wire-800 bg-ink-950 p-4 sm:p-5 flex flex-col gap-4 sm:gap-5 overflow-y-auto">
      <NarrativeCard
        aoi={activeAoi} bundle={bundle}
        watchOrder={watchOrder} selectedRow={selectedRow} onFocusBuilding={onFocusBuilding}
      />
      <ExportButton bundle={bundle} aoiCode={activeAoi.aoi_code} />
      <PhenomenonLegend aoi={activeAoi} mode={mode} idle={idle} />
      <div ref={threatAnchorRef}><Divider /></div>
      {selectedRow === null ? (
        <NoSelection />
      ) : (
        <SelectedBuilding bundle={bundle} row={selectedRow} monthIdx={monthIdx} />
      )}
      <DataProvenanceNote provenance={bundle.header.data_provenance} />
    </aside>
  );
}


/**
 * "Download CSV" — exports the current AOI's per-building screening table, built
 * client-side from the in-memory bundle (no backend call; the read app is untouched).
 * Bulk export is the strongest "commercial use" tell, so each download also reports an
 * insar_export event to Weespas's metering spine (inert for anonymous visitors — see
 * lib/telemetry.ts). An individual checking one building never clicks this; a bank
 * pulling the whole portfolio does.
 */
function ExportButton({ bundle, aoiCode }: { bundle: Bundle; aoiCode: string }) {
  const onDownload = () => {
    const csv = bundleToCsv(bundle, aoiCode);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `weespas-insar-${aoiCode}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    meterExport(aoiCode, bundle.buildingId.length);
  };
  return (
    <button
      type="button"
      onClick={onDownload}
      className="w-full rounded-md border border-wire-800 bg-ink-900 hover:bg-ink-800 hover:border-signal-cyan/60 transition-colors px-3 py-2 text-xs font-mono uppercase tracking-wide text-slate-300 hover:text-signal-cyan flex items-center justify-center gap-2"
      title="Download this area's per-building screening data as CSV"
    >
      <span aria-hidden>↓</span> Download CSV ({bundle.buildingId.length} buildings)
    </button>
  );
}


/**
 * Data-provenance footer. Branches on the bundle header's `data_provenance`
 * (three-state ladder, see backend/scripts/provenance.py):
 *   - 'insar'     → velocity + drift are real Sentinel-1 InSAR; soil class is
 *                   real SoilGrids WRB; shoreline / riparian distance are real
 *                   OSM-derived geometry; reclaimed-land is derived from the real
 *                   soil map. Built-year is the real OSM tag or absent (Ref_One
 *                   Phase 1, no synthetic env data — see postprocess.py honesty note).
 *   - 'partial'   → real footprints/geometry, but velocity is the synthetic
 *                   stand-in until the MintPy SBAS run lands. Env context is
 *                   also modeled.
 *   - 'synthetic' → everything fabricated, conforming to the production schema.
 * The point is that as each AOI gains real data the disclaimer self-updates to
 * say exactly what's real — no over- or under-claiming, no code change.
 */
function DataProvenanceNote({ provenance }: { provenance: DataProvenance }) {
  const badge =
    provenance === "insar"
      ? { cls: "border-green-500/50 bg-green-950/20 text-green-300", label: "live · Sentinel-1 InSAR" }
      : provenance === "partial"
      ? { cls: "border-sky-500/50 bg-sky-950/20 text-sky-300", label: "real footprints + terrain · synthetic velocity" }
      : { cls: "border-amber-500/40 bg-amber-950/20 text-amber-300", label: "synthetic preview" };

  return (
    <div className="mt-auto text-[10px] leading-relaxed">
      <div className={[
        "inline-block px-1.5 py-0.5 mb-1 border uppercase tracking-widest text-[9px]",
        badge.cls,
      ].join(" ")}>
        {badge.label}
      </div>
      <div className="text-wire-500">
        Source: Sentinel-1 SLC → HyP3 InSAR → MintPy SBAS.<br />
        {provenance === "insar" ? (
          <>
            Velocity &amp; drift are real InSAR measurements relative to the ⚓
            reference point; soil class (SoilGrids), shoreline / riparian distance
            (OSM geometry) and reclaimed-land (from soil) are{" "}
            <span className="text-green-300">all measured</span>. Built-year is the
            real OSM tag where mapped, otherwise omitted — nothing is fabricated.
          </>
        ) : provenance === "partial" ? (
          <>
            Building footprints &amp; geometry are real; velocity and the
            environmental factors (soil, shoreline/riparian distance) are
            <span className="text-amber-300"> modeled</span> pending real-data integration.
          </>
        ) : (
          <>Demo build uses synthetic data conforming to the production schema.</>
        )}
        <br />
        See <code className="text-slate-400">docs/risk_model.md</code>.
      </div>
    </div>
  );
}


function SkeletonSidebar() {
  return (
    <aside className="w-full lg:w-[380px] flex-1 lg:flex-none shrink-0 border-t lg:border-t-0 lg:border-l border-wire-800 bg-ink-950 p-4 sm:p-5">
      <div className="text-xs uppercase tracking-[0.3em] text-slate-600 animate-pulse">awaiting bundle…</div>
    </aside>
  );
}


function Divider() { return <div className="border-t border-wire-800" />; }


function NarrativeCard({
  aoi, bundle, watchOrder, selectedRow, onFocusBuilding,
}: {
  aoi: AoiSummary; bundle: Bundle;
  watchOrder: number[]; selectedRow: number | null;
  onFocusBuilding: (row: number) => void;
}) {
  // Aggregate stats: O(n) over n_buildings, fine for ~1500.
  // "confirmed" only counts CONFIRMED_THREAT — the previous heuristic
  // (v<-10) over-counted because it ignored coherence.
  let confirmed = 0, mixed = 0, noise = 0;
  const n = bundle.header.n_buildings;
  for (let i = 0; i < n; i++) {
    const cls = bundle.classification[i];
    if (cls === Classification.CONFIRMED_THREAT) confirmed++;
    else if (cls === Classification.MIXED_SIGNAL) mixed++;
    else if (cls === Classification.ENV_NOISE)   noise++;
  }

  // WATCH navigation: clicking the stat jumps to the worst MIXED building; the
  // ‹/› stepper walks the worst-first order. Position is derived from where the
  // current selection sits in watchOrder (−1 if the selection isn't a WATCH bldg).
  const watchPos = selectedRow == null ? -1 : watchOrder.indexOf(selectedRow);
  const stepWatch = (delta: number) => {
    if (watchOrder.length === 0) return;
    // From "nothing / non-watch selected", ‹ and › both start at the worst (idx 0).
    const next = watchPos < 0
      ? 0
      : (watchPos + delta + watchOrder.length) % watchOrder.length;
    onFocusBuilding(watchOrder[next]);
  };

  return (
    <div>
      <div className="text-xs uppercase tracking-[0.25em] text-slate-500">{aoi.name}</div>
      <div className="mt-2 grid grid-cols-4 gap-2 text-xs">
        <Stat label="buildings" value={n.toString()} />
        <Stat label="confirmed" value={confirmed.toString()} severe={confirmed > n * 0.1} />
        <Stat
          label="watch" value={mixed.toString()}
          onClick={mixed > 0 ? () => stepWatch(1) : undefined}
          title={mixed > 0 ? "Jump to the highest-risk WATCH building" : undefined}
        />
        <Stat label="noise"     value={noise.toString()} />
      </div>
      {watchOrder.length > 0 && (
        <div className="mt-2 flex items-center gap-2 text-[10px] text-amber-300/90">
          <button
            onClick={() => stepWatch(-1)}
            className="px-1.5 py-0.5 border border-amber-500/40 hover:bg-amber-950/40 leading-none"
            title="Previous WATCH building"
          >‹</button>
          <span className="tabular-nums text-slate-400">
            {watchPos >= 0 ? `WATCH ${watchPos + 1} / ${watchOrder.length}` : `${watchOrder.length} WATCH buildings`}
          </span>
          <button
            onClick={() => stepWatch(1)}
            className="px-1.5 py-0.5 border border-amber-500/40 hover:bg-amber-950/40 leading-none"
            title="Next WATCH building"
          >›</button>
          <span className="text-slate-500 normal-case">trustworthy · non-linear movers</span>
        </div>
      )}
      <p className="mt-3 text-[11px] text-slate-400 leading-relaxed">{aoi.narrative}</p>
    </div>
  );
}


function PhenomenonLegend({ aoi, mode, idle }: { aoi: AoiSummary; mode: ViewMode; idle: boolean }) {
  const isDrift = mode === "drift";
  // At rest the map paints each building's composite-risk RANK within this AOI
  // (green = lowest, red = highest), not month-0 velocity — so the legend must
  // describe the rank ramp, not mislabel it as a velocity scale.
  if (idle) {
    return (
      <div>
        <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
          Showing: overall risk rank
        </div>
        <div className="h-2 w-full rounded-sm" style={{
          background: "linear-gradient(90deg, #22c55e 0%, #f59e0b 50%, #ef4444 100%)",
        }} />
        <div className="flex justify-between text-[10px] text-slate-500 mt-1">
          <span>lowest risk</span><span>highest in this area</span>
        </div>
        <div className="mt-2 text-[10px] text-slate-500 italic">
          Each building is ranked against the others here — a composite verdict,
          not a velocity. Press play or scrub the timeline to switch this map to
          month-by-month {isDrift ? "drift" : "velocity"}, where the colour scale
          (and its meaning) changes.
        </div>
        <div className="mt-3 text-[10px] text-slate-500">
          Footprints: <span className="text-slate-300">{aoi.footprint_source}</span> ·{" "}
          Phenomenon: <span className="text-slate-300">{aoi.phenomenon.replace(/_/g, " ")}</span>
        </div>
        <div className="mt-2 text-[10px] text-slate-500 italic">
          Low-coherence footprints are desaturated — interpretation is uncertain.
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
        Showing: {isDrift ? "horizontal drift (east-west)" : "vertical velocity"}
      </div>
      <div className="h-2 w-full rounded-sm" style={{
        background: isDrift
          ? "linear-gradient(90deg, #38bdf8 0%, #64748b 50%, #f97316 100%)"
          : "linear-gradient(90deg, #ef4444 0%, #f59e0b 55%, #22c55e 100%)",
      }} />
      {/* Endpoint labels MUST match the ramp saturation bounds in RiskMap.tsx
          (EW_VELOCITY_BOUND = 5, VELOCITY_FLOOR_MM_YR = -10, CEIL = +5), which are
          tuned to the real InSAR value range. Keep in sync if those change. */}
      <div className="flex justify-between text-[10px] text-slate-500 mt-1 tabular-nums">
        {isDrift
          ? <><span>← 5 mm/yr west</span><span>stable</span><span>east 5 →</span></>
          : <><span>-10 mm/yr</span><span>0</span><span>+5</span></>}
      </div>
      {isDrift ? (
        <div className="mt-2 text-[10px] text-slate-500 italic">
          Diverging scale: grey = stable, colour intensity = drift speed —
          <span className="text-sky-400"> blue moving west</span>,
          <span className="text-orange-500"> orange moving east</span>. Footprint
          offset echoes it: taller building × faster drift = larger visual lean.
        </div>
      ) : (
        <div className="mt-2 text-[10px] text-slate-500 italic">
          Signed velocity: red = subsiding fastest, green = stable/rising. This is
          the opposite direction from the overall risk-rank scale shown at rest.
        </div>
      )}
      <div className="mt-3 text-[10px] text-slate-500">
        Footprints: <span className="text-slate-300">{aoi.footprint_source}</span> ·{" "}
        Phenomenon: <span className="text-slate-300">{aoi.phenomenon.replace(/_/g, " ")}</span>
      </div>
      <div className="mt-2 text-[10px] text-slate-500 italic">
        Low-coherence footprints are desaturated — interpretation is uncertain.
      </div>
    </div>
  );
}


function NoSelection() {
  return (
    <div>
      <div className="text-xs uppercase tracking-[0.25em] text-slate-500">Structural Threat</div>
      <div className="text-sm text-slate-400 mt-2">Click a building to inspect.</div>
    </div>
  );
}


function SelectedBuilding({
  bundle, row, monthIdx,
}: { bundle: Bundle; row: number; monthIdx: number }) {
  const m_last = bundle.header.n_months - 1;
  const v_now  = velocityAt(bundle, row, monthIdx);
  const d_now  = displacementAt(bundle, row, monthIdx);
  const coh    = coherenceAt(bundle, row, monthIdx);
  const ew_now = horizontalVelocityAt(bundle, row, monthIdx);
  const v_end  = velocityAt(bundle, row, m_last);
  const ew_end = horizontalVelocityAt(bundle, row, m_last);
  const bid    = bundle.buildingId[row];
  const accel  = bundle.velocityAccelMmYr2[row];
  const failureMode = bundle.failureMode[row];

  const ripa = bundle.riparianDistM[row];
  const shore = bundle.shorelineDistM[row];
  const reclaimed = bundle.reclaimedLand[row] === 1;
  const heightFloor = bundle.heightM[row];
  const heightInsar = bundle.insarHeightM[row];
  const heightSigma = bundle.insarHeightSigmaM[row];
  const heightFused = bundle.fusedHeightM[row];
  const heightImputed = bundle.heightImputed[row] === 1;
  const pixelShare = bundle.insarPixelShare[row];
  const composite = bundle.compositeRisk[row];
  const dangerLevel = bundle.dangerLevel[row];
  // A certifier (engineer/authority) has recorded an on-the-ground assessment for this
  // building. This is GROUND-VERIFIED PROVENANCE, not a safety verdict — the danger
  // badge below is independent and still shows red if the building is unsafe.
  const groundConfirmed = (bundle.structuralFlagState?.[row] ?? 0) !== 0;

  const trendSlope    = bundle.trendSlopeMmYr[row];
  const vSigma        = bundle.velocitySigmaMmYr[row];
  const ewSigma       = bundle.velocityEwSigmaMmYr[row];

  // Recompose the MOVEMENT sub-scores exactly as postprocess.composite_risk
  // does, so the stacked bar's segments reflect the real (movement-dominant)
  // model: movement sets the magnitude, susceptibility only amplifies it.
  // Anchors mirror the backend named constants (COLLAPSE_* in postprocess.py).
  // NaN-means-unknown: an unmeasured term contributes exactly 0 (no phantom).
  const subsScore  = clamp01(-v_end / 20);                                  // W_SUBS  0.45
  const accelScore = Number.isFinite(accel) ? clamp01(-accel / 8) : 0;      // W_ACCEL 0.25
  const shearScore = Number.isFinite(ew_end)                               // W_SHEAR 0.20 (nominal)
    ? clamp01((Math.abs(ew_end) - 1.5) / 4) : 0;                            //   knee 1.5, span 4
  const curveScore = Number.isFinite(trendSlope) ? clamp01(-trendSlope / 15) : 0; // W_CURVE 0.10
  // Confidence-scale the shear weight by the decomposition σ and hand the freed
  // weight to the vertical (subs) term — mirrors postprocess.composite_risk
  // (COLLAPSE_VEW_SIGMA_CLEAN 1.0 → NOISY 5.0). NaN σ_ew ⇒ conf=1 (full nominal).
  const shearConf  = Number.isFinite(ewSigma) ? clamp01((5.0 - ewSigma) / 4.0) : 1;
  const wShear     = 0.20 * shearConf;
  const wSubs      = 0.45 + (0.20 - wShear);
  // Susceptibility multiplier (amplify-only) — shown as a separate ×factor,
  // never as a stacked slice, because it cannot create risk on a still building.
  const proxScore = ripa >= 0 ? Math.exp(-ripa / 400)
                   : shore >= 0 ? Math.exp(-shore / 300)
                   : 0;
  const soilLoaded = Math.min(1, guessSoilScore(bundle, row) * (1 + Math.max(0, heightFused) / 10));
  const suscUplift = 0.30 * clamp01(0.6 * soilLoaded + 0.4 * proxScore);   // S_mult = 1 + uplift

  const series = buildingSeries(bundle, row, "displacement");
  const trendSeries = buildingSeries(bundle, row, "trend");
  const seasonalAmp   = bundle.seasonalAmplitudeMm[row];
  const trendR2       = bundle.trendR2[row];

  // Tier 3: cohort percentile context (velocity σ read above for shear-confidence).
  const cohortComp    = bundle.cohortCompositePct[row];
  const cohortShear   = bundle.cohortShearPct[row];
  const cohortN       = bundle.cohortSize[row];

  // ARCHITECTURE_THREE C1/C4 — block context: this building's block aggregates
  // + its block-relative percentile. Indexed off the per-building block_id.
  const blkId         = bundle.blockId[row];
  const blkCount      = bundle.blockCount[blkId] ?? 0;
  const blkWorstVel   = bundle.blockWorstVelocity[blkId] ?? 0;
  const blkConfirmed  = bundle.blockConfirmed[blkId] ?? 0;
  const cohortBlock   = bundle.cohortBlockPct[row];

  const severe = v_now < VEL_SEVERE_MM_YR;
  const driftSevere = Math.abs(ew_now) > 5;
  return (
    <div>
      <div className="text-xs uppercase tracking-[0.25em] text-slate-500">Structural Threat</div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <div className="text-sm text-slate-400">Building #{bid}</div>
        {groundConfirmed && <ConfirmedBadge />}
      </div>

      <ClassificationBadge dangerLevel={dangerLevel} failureMode={failureMode} vEwEnd={ew_end} accel={accel}
        vSigma={vSigma} sigmaMax={bundle.header.sigma_max} />

      <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
        <Metric label="Subsidence V"    value={fmtMm(v_now)}        unit="mm/yr" severe={severe}      sigma={vSigma} />
        <Metric label="Horizontal drift" value={fmtDrift(ew_now)}    unit="mm/yr" severe={driftSevere} sigma={ewSigma} />
        <Metric label="Cum displ"        value={fmtMm(d_now)}        unit="mm" />
        <Metric label="Coherence"        value={coh.toFixed(2)}      severe={coh < 0.3} />
        <TrendMetric accel={accel} />
        {/* Shoreline/riparian distances are REAL OSM-derived geometry (Ref_One
            Phase 1): join_insar.build_real_env supplies fetch_shoreline_dist_m /
            fetch_riparian_dist_m and an orientation self-check guards against the
            old inverted ramp (Finding A). Shown as measured. They are NaN/<0 for
            AOIs that genuinely lack the feature (inland → no coast, no waterway),
            in which case the guard hides the metric. reclaimed-land is now derived
            from the REAL soil map (soil_class == "reclaim_fill") → measured. */}
        {ripa >= 0 && <Metric label="Riparian dist"  value={ripa.toFixed(0)}  unit="m" />}
        {shore >= 0 && <Metric label="Shoreline dist" value={shore.toFixed(0)} unit="m" />}
        {reclaimed && <Metric label="Reclaimed land" value="YES" severe />}
      </div>

      <HeightCard
        floor={heightFloor}
        insar={heightInsar}
        sigma={heightSigma}
        fused={heightFused}
        imputed={heightImputed}
        pixelShare={pixelShare}
      />

      <div className="mt-4">
        <div className="flex justify-between text-[10px] uppercase tracking-widest text-slate-500">
          <span>Collapse score</span>
          <span className="tabular-nums text-slate-300">{(composite * 100).toFixed(0)}%</span>
        </div>
        <StackedRiskBar
          composite={composite}
          subs={wSubs * subsScore}
          accel={0.25 * accelScore}
          shear={wShear * shearScore}
          curve={0.10 * curveScore}
        />
        {/* Declutter: only legend the drivers that are actually present in this
            building's bar, plus the amplify/floor segment when it shows. Inline
            flex-wrap (not a fixed 4-col grid) so empty cells never leave gaps. */}
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-slate-500">
          {RISK_SEGMENTS.map((seg, i) =>
            [subsScore, accelScore, shearScore, curveScore][i] > 0 ? (
              <span key={seg.key}><Dot c={seg.color} /> {seg.label}</span>
            ) : null,
          )}
          {composite > 0.45 * subsScore + 0.25 * accelScore + wShear * shearScore + 0.10 * curveScore + 1e-3 && (
            <span><Dot c={UPLIFT_COLOR} /> amplify/floor</span>
          )}
        </div>
        <p className="mt-1 text-[10px] text-slate-500 leading-snug">
          Bar fills to the score; InSAR movement drives it, ground susceptibility{" "}
          <span className="tabular-nums text-slate-400">×{(1 + suscUplift).toFixed(2)}</span>{" "}
          only amplifies a building already moving.
        </p>
        <CohortContext
          compositePct={cohortComp}
          shearPct={cohortShear}
          cohortN={cohortN}
        />
        <BlockContext
          blockPct={cohortBlock}
          count={blkCount}
          worstVelocity={blkWorstVel}
          confirmed={blkConfirmed}
        />
      </div>

      <div className="mt-4">
        <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">
          {bundle.header.n_months}-mo displacement (mm) · trend overlay
        </div>
        <Sparkline values={series} trend={trendSeries} highlightIdx={monthIdx} />
        <FailureModeLine
          mode={failureMode}
          trendSlope={trendSlope}
          seasonalAmp={seasonalAmp}
          r2={trendR2}
        />
      </div>
    </div>
  );
}


/**
 * Threat badge — SINGLE SOURCE OF TRUTH is the absolute `dangerLevel`
 * (postprocess.danger_level), so the badge tier, the heat-map ranking, and the
 * cross-AOI scale can never disagree. The tier picks the urgency word + colour;
 * PLASTIC / lateral-shear / acceleration only refine the WORDING within a tier
 * (e.g. "progressive shear failure" vs "progressive subsidence"), never the tier.
 *
 * Tiers (cross-AOI comparable, sensitive cutoffs — see postprocess.danger_level):
 *   CRITICAL → "Act now"     (red)    — PLASTIC, hard acceleration, or severe subsidence
 *   HIGH     → "Act soon"    (red)    — confirmed movement / strong drift
 *   ELEVATED → "Watch"       (amber)  — moderate movement
 *   LOW      → "Monitor"     (amber)  — slight movement
 *   STABLE   → "Ground stable" (green) — no GROUND movement (not a structural
 *                                        all-clear; see scope caveat below badge)
 *
 * The ConfidencePill (σ) rides alongside, so a σ-untrustworthy reading is shown
 * honestly without being hidden — that is what lets a real PLASTIC failure read
 * CRITICAL even when its σ is high.
 */
/**
 * "Confirmed" badge — a certifier (engineer/authority) recorded an on-the-ground
 * structural assessment for this building. It is a GROUND-VERIFIED PROVENANCE marker
 * ("a human looked at this"), NOT a safety verdict: it can sit next to a red danger
 * badge on a building an authority condemned. The title spells that out so the green
 * can never be misread as "safe". Green tokens match the STABLE ClassificationBadge.
 */
function ConfirmedBadge() {
  return (
    <span
      title="Confirmed by an on-the-ground assessment"
      className="inline-flex items-center gap-1 px-2 py-0.5 border border-green-500/40 bg-green-950/20 text-green-300 text-[10px] uppercase tracking-widest whitespace-nowrap"
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        <path d="M9 12l2 2 4-4" />
      </svg>
      Confirmed
    </span>
  );
}

function ClassificationBadge({
  dangerLevel, failureMode, vEwEnd, accel, vSigma, sigmaMax,
}: { dangerLevel: number; failureMode: number; vEwEnd: number; accel: number;
     vSigma: number; sigmaMax: number | null }) {
  const isPlastic = failureMode === FailureMode.PLASTIC;
  const shearDominant = Math.abs(vEwEnd) > 2.5;
  const accelerating = Number.isFinite(accel) && accel < ACCEL_HIGH_MM_YR2;

  // Wording refinement WITHIN the chosen tier — describes the failure mechanism,
  // never changes the urgency tier (that's danger_level's job alone).
  const mechanism =
      isPlastic && shearDominant ? "progressive shear failure"
    : isPlastic                  ? "progressive subsidence"
    : shearDominant              ? "lateral shear"
    : accelerating               ? "accelerating, non-linear movement"
    :                              "confirmed movement";

  let text: string;
  let bg: string;
  switch (dangerLevel) {
    case DangerLevel.CRITICAL:
      text = `Act now · ${mechanism}`;
      bg = "bg-red-950/50 border-red-500/80 text-red-200";
      break;
    case DangerLevel.HIGH:
      text = `Act soon · ${mechanism}`;
      bg = "bg-red-950/30 border-red-500/50 text-red-300";
      break;
    case DangerLevel.ELEVATED:
      text = "Watch · movement detected";
      bg = "bg-amber-950/30 border-amber-500/40 text-amber-300";
      break;
    case DangerLevel.LOW:
      text = "Monitor · slight movement";
      bg = "bg-amber-950/20 border-amber-400/40 text-amber-200";
      break;
    default: // STABLE
      text = "Ground stable · no movement detected";
      bg = "bg-green-950/30 border-green-500/40 text-green-300";
      break;
  }
  return (
    <div className="mt-2">
      <div className="flex items-stretch gap-1.5">
        <div className={["flex-1 px-2 py-1 border text-[10px] uppercase tracking-widest", bg].join(" ")}>
          {text}
        </div>
        <ConfidencePill vSigma={vSigma} sigmaMax={sigmaMax} />
      </div>
      {/* Life-safety honesty: InSAR sees GROUND/foundation deformation only. A
          "ground stable" verdict is NOT a structural all-clear — overload,
          under-design, bad materials and construction-stage failures (the South
          C class) do not move the ground first and are invisible here. State the
          scope so a green badge can never be read as "this building is safe". */}
      {dangerLevel === DangerLevel.STABLE && (
        <div className="mt-1 px-2 py-1 border border-wire-700 bg-ink-800/50 text-[9px] leading-snug text-slate-400 normal-case tracking-normal">
          Scope: ground/foundation movement only. Not a structural-capacity
          assessment — overload, under-design or material failure are not
          visible to InSAR.
        </div>
      )}
    </div>
  );
}

/**
 * Confidence pill: the velocity error margin as a number the user can read and
 * defend, tinted green/yellow/red for glance-triage. The number is σ (velocity
 * standard error, mm/yr); the colour compares it to THIS AOI's gate (sigma_max):
 *   green  σ ≤ sigma_max       — inside the trustworthy band the gate accepts
 *   yellow sigma_max < σ ≤ 2×   — above gate but interpretable
 *   red    σ > 2×sigma_max      — unreliable; an "Act now" here is caveated
 * This is what lets a σ-fail-but-PLASTIC building read "Act now" honestly: the
 * pill states the uncertainty in the same breath. sigma_max null (empty AOI) or
 * non-finite σ → neutral grey, no false precision.
 */
function ConfidencePill({ vSigma, sigmaMax }: { vSigma: number; sigmaMax: number | null }) {
  if (!Number.isFinite(vSigma)) return null;
  // Adaptive rounding so the pill never overflows on a wild future AOI.
  const num =
    Math.abs(vSigma) < 10  ? vSigma.toFixed(2) :
    Math.abs(vSigma) < 100 ? vSigma.toFixed(1) :
                             vSigma.toFixed(0);
  let tint: string;
  if (sigmaMax == null || !Number.isFinite(sigmaMax)) {
    tint = "border-wire-700 bg-ink-800/60 text-slate-400";
  } else if (vSigma <= sigmaMax) {
    tint = "border-green-500/40 bg-green-950/30 text-green-300";
  } else if (vSigma <= 2 * sigmaMax) {
    tint = "border-amber-500/40 bg-amber-950/30 text-amber-300";
  } else {
    tint = "border-red-500/50 bg-red-950/40 text-red-300";
  }
  return (
    <div
      className={["px-2 py-1 border text-[10px] tracking-wider whitespace-nowrap self-center", tint].join(" ")}
      title="Velocity error margin (σ). Green = within this area's reliability gate."
    >
      ±{num} mm/yr
    </div>
  );
}


/**
 * Trend cell: shows velocity acceleration with directional arrow.
 * Uses the backend acceleration cutoff (ACCEL_HIGH_MM_YR2 = −8 mm/yr², ≈1σ above
 * the real InSAR noise floor) so a building reads "accelerating" only when its
 * own danger_level badge would agree. The old ±3 cutoff was tuned to the
 * synthetic generator and fired on real-data noise.
 */
function TrendMetric({ accel }: { accel: number }) {
  const accelerating = accel < ACCEL_HIGH_MM_YR2;
  const decelerating = accel > ACCEL_DECEL_MM_YR2;
  const label =
    accelerating ? "▲ accelerating" :
    decelerating ? "▼ decelerating" :
                   "→ steady";
  return (
    <div className={[
      "p-3 border",
      accelerating ? "border-red-500/60 bg-red-950/30" : "border-wire-800 bg-ink-800/40",
    ].join(" ")}>
      <div className="text-[10px] uppercase tracking-widest text-slate-500">Trend</div>
      <div className={[
        "text-base tabular-nums",
        accelerating ? "text-red-300" : "text-slate-100",
      ].join(" ")}>
        {label}
        <span className="text-xs text-slate-500 ml-1">
          {accel >= 0 ? "+" : ""}{accel.toFixed(1)} mm/yr²
        </span>
      </div>
    </div>
  );
}


/** LUT-only soil score for visualization (we don't pack the soil class as a
 * numeric per-building field; soil_classes lives in the JSON header indexed
 * by row order, but in practice the soil weight is small and the recomposed
 * bar is illustrative not authoritative — the *composite* is canonical). */
function guessSoilScore(bundle: Bundle, row: number): number {
  const cls = bundle.header.soil_classes[row] || "";
  const lut: Record<string, number> = {
    black_cotton: 0.9, alluvial: 0.7, red_clay: 0.4, weathered_basalt: 0.1,
    coral_rag: 0.15, reclaim_fill: 0.85,
  };
  return lut[cls] ?? 0.3;
}


/**
 * Height estimate breakdown — and an honest account of WHERE the number comes
 * from, because height feeds the collapse score's load factor.
 *
 * Two data regimes:
 *  - REAL InSAR join (today, all 5 AOIs): there is NO InSAR-inverted height yet
 *    (the phase-fringe inversion is unbuilt), so `insar` arrives NaN and `fused`
 *    just equals the floor-count/source height. We render a single honest height
 *    line — no fabricated "InSAR 0.0 ± 0.0" row, no false 2σ disagreement flag.
 *  - SYNTHETIC seed: `insar` is finite, so the full 3-way breakdown
 *    (floor-count / InSAR ± σ / fused) renders as before.
 *
 * Two honesty caveats ride alongside:
 *  - `imputed`: the source had no height; the value is estimated (neighbourhood
 *    median or floor-count), and the load factor uses that estimate.
 *  - `pixelShare`: how many buildings share this footprint's ~78 m InSAR cell.
 *    The HyP3 cell dwarfs a single footprint, so when >1 the velocity is the
 *    cell average, not building-specific — the shape is crisp, the signal isn't.
 */
function HeightCard({
  floor, insar, sigma, fused, imputed, pixelShare,
}: { floor: number; insar: number; sigma: number; fused: number;
     imputed: boolean; pixelShare: number }) {
  if (!Number.isFinite(fused) || fused <= 0) return null;
  // Only treat the InSAR estimate as real when it's a finite, positive height
  // with a finite positive σ. On the real path it's NaN → this whole branch is
  // skipped (no phantom "0.0 ± 0.0" line, no always-true disagreement check).
  const hasInsar = Number.isFinite(insar) && insar > 0 && Number.isFinite(sigma) && sigma > 0;
  const disagreeHigh = hasInsar && Math.abs(insar - floor) > 2 * sigma;
  const shared = Number.isFinite(pixelShare) && pixelShare > 1;
  return (
    <div className="mt-4 border border-wire-800 bg-ink-800/40 p-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-500">Building height</div>
      {hasInsar ? (
        <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
          <HeightLine label="Floor-count"   value={`${floor.toFixed(1)} m`} />
          <HeightLine label="InSAR"         value={`${insar.toFixed(1)} ± ${sigma.toFixed(1)} m`}
                      flagged={disagreeHigh} />
          <HeightLine label="Fused (3D)"    value={`${fused.toFixed(1)} m`} accent />
        </div>
      ) : (
        <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
          <HeightLine label={imputed ? "Estimated height" : "Floor-count height"}
                      value={`${fused.toFixed(1)} m`} accent flagged={imputed} />
          <HeightLine label="Floors (~3 m)" value={`${Math.max(1, Math.round(fused / 3))}`} />
        </div>
      )}
      {disagreeHigh && (
        <div className="mt-2 text-[10px] text-amber-400/80 italic">
          Footprint and InSAR disagree by more than 2σ. Likely noisy phase
          fringes (small footprint or low coherence).
        </div>
      )}
      {imputed && (
        <div className="mt-2 text-[10px] text-amber-400/80 italic">
          Height absent from the source footprint — imputed from the
          neighbourhood. The collapse-score load factor uses this estimate.
        </div>
      )}
      {shared && (
        <div className="mt-2 text-[10px] text-slate-500 italic">
          InSAR cell shared by {pixelShare} buildings (~78 m Sentinel-1
          resolution). Velocity &amp; coherence here are the cell average — the
          footprint is exact, the movement signal is not building-specific.
        </div>
      )}
    </div>
  );
}


function HeightLine({
  label, value, accent, flagged,
}: { label: string; value: string; accent?: boolean; flagged?: boolean }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-widest text-slate-500">{label}</div>
      <div className={[
        "tabular-nums",
        accent ? "text-signal-cyan" : flagged ? "text-amber-300" : "text-slate-200",
      ].join(" ")}>
        {value}
      </div>
    </div>
  );
}


// Fixed palette for the movement drivers — single source of truth for both the
// bar segments and the inline legend, so a colour can never drift between them.
const RISK_SEGMENTS = [
  { key: "subs",  color: "#ef4444", label: "subsidence" },
  { key: "accel", color: "#f59e0b", label: "acceleration" },
  { key: "shear", color: "#a78bfa", label: "shear" },
  { key: "curve", color: "#22d3ee", label: "non-linearity" },
] as const;
const UPLIFT_COLOR = "#64748b"; // slate — "everything the drivers don't explain"

// The bar fills to `composite` itself (the authoritative score shown as the %),
// so the fill and the number are ALWAYS equal — they cannot diverge by
// construction, regardless of floors/gates/multipliers the client can't see.
// The four InSAR movement drivers partition the part of `composite` they explain;
// any remainder (susceptibility ×, PLASTIC/protect floor, tilt, classification
// gates) is shown as ONE honest "amplify/floor" segment rather than silently
// dropped. O(1): a fixed number of segments, percentage widths (responsive).
function StackedRiskBar({
  composite, subs, accel, shear, curve,
}: { composite: number; subs: number; accel: number; shear: number; curve: number }) {
  const s = (x: number) => (Number.isFinite(x) && x > 0 ? x : 0);
  const comp = clamp01(s(composite));
  const drivers = [s(subs), s(accel), s(shear), s(curve)];
  const mCont = drivers.reduce((a, b) => a + b, 0);
  // Scale the drivers so they never overflow `composite` (the floor/multiplier can
  // make composite < m_cont, e.g. a gated building); the leftover up to composite
  // is the uplift segment. Both expressed as % of the full bar.
  const driverScale = mCont > comp && mCont > 0 ? comp / mCont : 1;
  const segs = RISK_SEGMENTS.map((seg, i) => ({
    ...seg, pct: drivers[i] * driverScale * 100,
  }));
  const upliftPct = Math.max(0, comp - mCont * driverScale) * 100;
  return (
    <div className="mt-1 h-2.5 w-full flex bg-wire-800 overflow-hidden" role="img"
         aria-label={`Collapse score ${(comp * 100).toFixed(0)} percent`}>
      {segs.map((seg) =>
        seg.pct > 0 ? <div key={seg.key} style={{ width: `${seg.pct}%`, background: seg.color }} /> : null,
      )}
      {upliftPct > 0 && <div style={{ width: `${upliftPct}%`, background: UPLIFT_COLOR }} />}
    </div>
  );
}


function Stat({
  label, value, severe, onClick, title,
}: { label: string; value: string; severe?: boolean; onClick?: () => void; title?: string }) {
  const base = `p-2 border text-left w-full ${severe ? "border-red-500/50 bg-red-950/20" : "border-wire-800 bg-ink-800/50"}`;
  const inner = (
    <>
      <div className="text-[9px] uppercase tracking-widest text-slate-500">{label}</div>
      <div className={`tabular-nums text-base ${severe ? "text-red-300" : "text-slate-100"}`}>{value}</div>
    </>
  );
  if (onClick) {
    return (
      <button onClick={onClick} title={title}
        className={`${base} hover:border-amber-500/60 hover:bg-amber-950/20 transition cursor-pointer`}>
        {inner}
      </button>
    );
  }
  return <div className={base}>{inner}</div>;
}


function Metric({
  label, value, unit, severe, sigma,
}: {
  label: string;
  value: string | number;
  unit?: string;
  severe?: boolean;
  /** Optional 1σ uncertainty (Tier 3); rendered as `± σ unit` in the unit slot. */
  sigma?: number;
}) {
  const sigmaText = sigma != null && Number.isFinite(sigma)
    ? `± ${sigma.toFixed(1)}${unit ? " " + unit : ""}`
    : null;
  return (
    <div className={`p-3 border ${severe ? "border-red-500/60 bg-red-950/30" : "border-wire-800 bg-ink-800/40"}`}>
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] uppercase tracking-widest text-slate-500">{label}</span>
      </div>
      <div className={`text-base tabular-nums ${severe ? "text-red-300" : "text-slate-100"}`}>
        {value}
        {sigmaText
          ? <span className="text-[10px] text-slate-500 ml-1 tabular-nums">{sigmaText}</span>
          : unit && <span className="text-xs text-slate-500 ml-1">{unit}</span>}
      </div>
    </div>
  );
}


function Dot({ c }: { c: string }) {
  return <span className="inline-block w-2 h-2 mr-1" style={{ background: c }} />;
}


function Sparkline({
  values, trend, highlightIdx,
}: { values: Float32Array; trend?: Float32Array; highlightIdx: number }) {
  if (values.length === 0) return null;
  const w = 320, h = 70, pad = 6;
  // Compute scale across both series so the overlay sits in the same frame.
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (trend) {
    for (let i = 0; i < trend.length; i++) {
      const v = trend[i];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  const range = hi - lo || 1;
  const xs = (i: number) => pad + (i / (values.length - 1)) * (w - 2 * pad);
  const ys = (v: number) => h - pad - ((v - lo) / range) * (h - 2 * pad);
  let path = "";
  for (let i = 0; i < values.length; i++) {
    path += `${i === 0 ? "M" : "L"}${xs(i).toFixed(1)},${ys(values[i]).toFixed(1)} `;
  }
  let trendPath = "";
  if (trend && trend.length === values.length) {
    for (let i = 0; i < trend.length; i++) {
      trendPath += `${i === 0 ? "M" : "L"}${xs(i).toFixed(1)},${ys(trend[i]).toFixed(1)} `;
    }
  }
  return (
    <svg width={w} height={h} className="text-signal-cyan">
      <path d={path} fill="none" stroke="currentColor" strokeWidth={1.4} />
      {trendPath && (
        <path d={trendPath} fill="none" stroke="#cbd5e1" strokeWidth={1.2}
              strokeDasharray="3 2" opacity={0.85} />
      )}
      <line x1={xs(highlightIdx)} x2={xs(highlightIdx)} y1={pad} y2={h - pad}
            stroke="#f59e0b" strokeDasharray="2 3" />
      <circle cx={xs(highlightIdx)} cy={ys(values[highlightIdx])} r={3} fill="#f59e0b" />
    </svg>
  );
}


/**
 * STL failure-mode classifier:
 *   ELASTIC = seasonal soil response (breathes with the rain — not failure)
 *   PLASTIC = progressive trend (foundation actually failing)
 *
 * Confidence caveat: 24 months is the statistical floor for STL with annual
 * seasonality. R² and seasonal amplitude are surfaced so the reader can judge
 * the call themselves — not hidden behind the badge.
 */
function FailureModeLine({
  mode, trendSlope, seasonalAmp, r2,
}: { mode: number; trendSlope: number; seasonalAmp: number; r2: number }) {
  const isPlastic = mode === FailureMode.PLASTIC;
  return (
    <div className={[
      "mt-2 px-2 py-1.5 border text-[10px] leading-snug",
      isPlastic
        ? "border-red-500/50 bg-red-950/30 text-red-200"
        : "border-wire-800 bg-ink-800/40 text-slate-300",
    ].join(" ")}>
      <div className="uppercase tracking-widest text-[9px] text-slate-500">
        Failure mode (STL · 24 mo)
      </div>
      <div className="mt-0.5 tabular-nums">
        {isPlastic
          ? <><span className="text-red-300">PLASTIC</span> · progressive foundation failure</>
          : <><span className="text-slate-200">ELASTIC</span> · seasonal soil response</>}
      </div>
      <div className="mt-0.5 text-slate-500 tabular-nums">
        trend {trendSlope >= 0 ? "+" : ""}{trendSlope.toFixed(1)} mm/yr  ·
        seasonal ±{(seasonalAmp / 2).toFixed(1)} mm  ·
        {r2 < 0
          ? <span className="text-amber-400/80 not-tabular-nums"> fit poor (R²&lt;0)</span>
          : <> R² {r2.toFixed(2)}{r2 < 0.5 && <span className="text-amber-400/80 not-tabular-nums"> · fit poor</span>}</>}
      </div>
    </div>
  );
}


/**
 * Cohort percentile context (Tier 3 #7 in ARCHITECTURE_TWO).
 *
 * A composite score in isolation ("0.62") is useless to a decision-maker. The
 * actionable framing is *relative to peers*: "92nd percentile for shear among
 * 47 buildings on the same soil at a similar height." The backend pre-computes
 * the percentile rank within `height_band × soil_class` cohorts; the UI's
 * job is just to render it as a sentence.
 *
 * Singleton cohorts (n=1) are tagged 50/50 by the backend — we suppress those
 * because "median of 1 peer" is misleading; render a quiet caveat instead.
 */
function CohortContext({
  compositePct, shearPct, cohortN,
}: { compositePct: number; shearPct: number; cohortN: number }) {
  if (cohortN <= 1) {
    return (
      <div className="mt-1 text-[10px] text-slate-500 italic">
        No peer cohort (unique height × soil bucket — percentile suppressed).
      </div>
    );
  }
  // Bold flag once a building is in the top quartile on either axis — that's
  // the threshold where "look at this one first" becomes the right call.
  const hot = compositePct >= 75 || shearPct >= 75;
  return (
    <div className={[
      "mt-2 px-2 py-1.5 border text-[10px] leading-snug tabular-nums",
      hot
        ? "border-amber-500/50 bg-amber-950/20 text-amber-200"
        : "border-wire-800 bg-ink-800/40 text-slate-300",
    ].join(" ")}>
      <div className="uppercase tracking-widest text-[9px] text-slate-500">
        Peer cohort · height-band × soil ({cohortN} buildings)
      </div>
      <div className="mt-0.5">
        composite <span className={hot ? "text-amber-200" : "text-slate-100"}>
          {ordinal(compositePct)}
        </span> pct · shear <span className={shearPct >= 75 ? "text-amber-200" : "text-slate-100"}>
          {ordinal(shearPct)}
        </span> pct
      </div>
    </div>
  );
}

/**
 * Block context (ARCHITECTURE_THREE C1/C4).
 *
 * Reframes the building against its ~170 m grid block — the honest unit of
 * InSAR resolution in dense Nairobi, where individual sub-pixel footprints
 * share a pixel. Shows how this building ranks *within its own block* plus the
 * block's headline aggregates (worst velocity, building count, confirmed
 * threats). Singleton blocks suppress the percentile, same as the peer cohort.
 */
function BlockContext({
  blockPct, count, worstVelocity, confirmed,
}: { blockPct: number; count: number; worstVelocity: number; confirmed: number }) {
  const hot = confirmed > 0 || worstVelocity < VEL_SEVERE_MM_YR;
  return (
    <div className={[
      "mt-2 px-2 py-1.5 border text-[10px] leading-snug tabular-nums",
      hot
        ? "border-red-500/40 bg-red-950/20 text-red-200"
        : "border-wire-800 bg-ink-800/40 text-slate-300",
    ].join(" ")}>
      <div className="uppercase tracking-widest text-[9px] text-slate-500">
        Block context · ~170 m grid ({count} building{count === 1 ? "" : "s"})
      </div>
      <div className="mt-0.5">
        {count > 1
          ? <>rank <span className={blockPct >= 75 ? "text-amber-200" : "text-slate-100"}>{ordinal(blockPct)}</span> pct in block · </>
          : <>sole building in block · </>}
        worst <span className={worstVelocity < VEL_SEVERE_MM_YR ? "text-red-300" : "text-slate-100"}>{worstVelocity.toFixed(1)}</span> mm/yr
      </div>
      {confirmed > 0 && (
        <div className="mt-0.5 text-red-300">
          {confirmed} confirmed threat{confirmed === 1 ? "" : "s"} in this block
        </div>
      )}
    </div>
  );
}

/** "92nd", "1st", "23rd" — for percentile labels. */
function ordinal(n: number): string {
  const v = Math.round(n);
  const mod100 = v % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${v}th`;
  switch (v % 10) {
    case 1:  return `${v}st`;
    case 2:  return `${v}nd`;
    case 3:  return `${v}rd`;
    default: return `${v}th`;
  }
}


function clamp01(x: number): number { return x < 0 ? 0 : x > 1 ? 1 : x; }
function fmtMm(v: number): string { return Number.isFinite(v) ? v.toFixed(1) : "—"; }
function fmtDrift(ew: number): string {
  if (!Number.isFinite(ew)) return "—";
  const dir = ew > 0.2 ? " E" : ew < -0.2 ? " W" : "";
  return `${ew >= 0 ? "+" : ""}${ew.toFixed(1)}${dir}`;
}
