import { useEffect, useRef, useState } from "react";
import { AoiSummary } from "../lib/useAois";
import { getReturnUrl } from "../lib/telemetry";

export type ViewMode = "subsidence" | "drift";

export function TopBar({
  aois, activeCode, onSelect, observationDate,
  mode, onModeChange, idle,
  showBlocks, onToggleBlocks,
  showWatch, onToggleWatch, watchCount,
}: {
  aois: AoiSummary[] | null;
  activeCode: string | null;
  onSelect: (code: string) => void;
  observationDate?: string;
  mode: ViewMode;
  onModeChange: (m: ViewMode) => void;
  idle: boolean;
  showBlocks: boolean;
  onToggleBlocks: (v: boolean) => void;
  showWatch: boolean;
  onToggleWatch: (v: boolean) => void;
  watchCount: number;
}) {
  return (
    <div className="absolute top-0 left-0 right-0 px-3 sm:px-6 py-3 bg-gradient-to-b from-ink-950/95 via-ink-950/70 to-transparent flex items-start justify-between gap-3 flex-wrap pointer-events-none">
      <div className="pointer-events-auto min-w-0">
        {/* "← Back to Weespas" — shown only when the user arrived via a Weespas deep-link
            (a validated same-origin return path was passed). A full-page link, since InSAR
            is a separate SPA from Weespas. NOTE: InSAR's top-bar UI/UX is due a redesign;
            this chip is intentionally minimal until then. */}
        <BackToWeespas />
        <div className="hidden sm:block text-[10px] uppercase tracking-[0.3em] text-slate-500 truncate">
          infra-proptech / structural deformation monitor
        </div>
        <div className="mt-1">
          <AoiDropdown aois={aois} activeCode={activeCode} onSelect={onSelect} />
        </div>
      </div>

      <div className="pointer-events-auto flex items-center gap-3 sm:gap-4 flex-wrap justify-end">
        <OverlayDropdown
          showBlocks={showBlocks} onToggleBlocks={onToggleBlocks}
          showWatch={showWatch} onToggleWatch={onToggleWatch} watchCount={watchCount}
        />
        <ModeDropdown mode={mode} onChange={onModeChange} idle={idle} />
        <div className="text-right shrink-0">
          <div className="text-[10px] uppercase tracking-[0.3em] text-slate-500">Observation</div>
          <div className="text-base sm:text-lg tabular-nums whitespace-nowrap leading-tight">{observationDate ?? "—"}</div>
        </div>
      </div>
    </div>
  );
}


/**
 * "Back to Weespas" breadcrumb. Renders nothing unless the visitor arrived from a Weespas
 * deep-link (getReturnUrl() is non-null — a validated same-origin path joined onto InSAR's
 * own configured Weespas origin). A plain anchor doing a full-page navigation: InSAR and
 * Weespas are separate SPAs on different origins, so there's no client router to hand off to.
 *
 * DESIGN: deliberately mirrors Weespas's `.stats-back` breadcrumb (the Profile→Staff/Stats/
 * Admin back-links) so the two apps feel like one product — a plain arrow-left + label link,
 * muted by default and brightening on hover, NO border / chip / uppercase. Colours are the
 * dark-theme inversion of Weespas's (slate-300 → white, vs secondary → text on light), since
 * this sits on the map's dark top gradient. Icon 18px + 8px gap match the Weespas spec.
 * The return URL is computed once at module load: a cheap conditional render, no listeners.
 */
function BackToWeespas() {
  const href = getReturnUrl();
  if (!href) return null;
  return (
    <a
      href={href}
      className={[
        "group inline-flex items-center gap-2 mb-2 text-sm font-medium",
        "text-slate-300 hover:text-white transition-colors",
      ].join(" ")}
      title="Return to Weespas"
    >
      <ArrowLeft />
      <span>Back to Weespas</span>
    </a>
  );
}

/** Arrow-left glyph for the breadcrumb, sized 18px to match Weespas's `<Icon name="arrowLeft"
 *  size={18} />`. Nudges left on hover for a subtle "going back" affordance. */
function ArrowLeft() {
  return (
    <svg
      width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"
      fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round"
      className="shrink-0 transition-transform group-hover:-translate-x-0.5"
    >
      <path d="M19 12H5" />
      <path d="m12 19-7-7 7-7" />
    </svg>
  );
}


/**
 * AOI selector as a single glass-morphism dropdown (replaces the horizontal
 * button row, which didn't scale past 2–3 AOIs — we now have 5). The trigger
 * shows the active AOI + a chevron that rotates when open; the menu lists every
 * AOI with the active one checked.
 *
 * Interaction cost is O(1): open/close flips one boolean, selection calls the
 * parent once. The outside-click / Escape listeners are attached ONLY while the
 * menu is open, so a closed dropdown adds zero global handlers.
 */
function AoiDropdown({
  aois, activeCode, onSelect,
}: {
  aois: AoiSummary[] | null;
  activeCode: string | null;
  onSelect: (code: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Close on outside-click or Escape — but only subscribe while open, so the
  // common (closed) state carries no document-level listeners.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = aois?.find(a => a.aoi_code === activeCode) ?? null;
  const label = active?.name ?? "Select area";

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        disabled={!aois?.length}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={active?.phenomenon}
        className={[
          "flex items-center gap-2 min-w-[12rem] px-3 py-1.5 text-xs uppercase tracking-widest",
          "rounded-md border backdrop-blur-md transition",
          "bg-signal-cyan/10 border-signal-cyan/60 text-signal-cyan",
          "hover:bg-signal-cyan/15 hover:border-signal-cyan",
          "disabled:opacity-40 disabled:cursor-not-allowed",
        ].join(" ")}
      >
        <span className="flex-1 text-left truncate">{label}</span>
        <Chevron open={open} />
      </button>

      {open && aois && (
        <ul
          role="listbox"
          className={[
            "absolute z-20 mt-1 min-w-full w-max max-w-xs py-1",
            "rounded-md border border-white/10 bg-ink-900/70 backdrop-blur-xl",
            "shadow-xl shadow-black/50 ring-1 ring-black/30",
          ].join(" ")}
        >
          {aois.map(a => {
            const isActive = a.aoi_code === activeCode;
            return (
              <li key={a.aoi_code} role="option" aria-selected={isActive}>
                <button
                  type="button"
                  onClick={() => { onSelect(a.aoi_code); setOpen(false); }}
                  className={[
                    "flex w-full items-center gap-2 px-3 py-1.5 text-xs uppercase tracking-widest text-left transition",
                    isActive
                      ? "text-signal-cyan bg-signal-cyan/10"
                      : "text-slate-300 hover:bg-white/5 hover:text-slate-100",
                  ].join(" ")}
                  title={a.phenomenon}
                >
                  <span className="w-3 shrink-0 text-signal-cyan">{isActive ? "✓" : ""}</span>
                  <span className="flex-1 truncate">{a.name}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}


/** Chevron that points down when closed, flips up when the menu opens. */
function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"
      className={`shrink-0 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
    >
      <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}


/**
 * Subsidence/Drift selector as a single dropdown (mirrors AoiDropdown), so the
 * top bar stays compact. Single-select, so the menu closes on pick. While the
 * map is idle (month 0, paused) it paints the composite risk-rank heat-map, not
 * per-epoch velocity, so this control has NO visible effect until the user
 * plays/scrubs — we dim the trigger and keep the "active on playback" hint so it
 * doesn't read as broken. Outside-click / Escape close, subscribed only while open.
 */
const MODE_LABELS: Record<ViewMode, string> = { subsidence: "Subsidence", drift: "Drift" };

function ModeDropdown({ mode, onChange, idle }: { mode: ViewMode; onChange: (m: ViewMode) => void; idle: boolean }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const modes: ViewMode[] = ["subsidence", "drift"];

  return (
    <div
      ref={rootRef}
      className={`relative inline-block ${idle ? "opacity-60" : ""} transition-opacity`}
      title={idle ? "Press play or scrub the timeline to see velocity in this mode" : undefined}
    >
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={[
          "flex items-center gap-2 min-w-[9rem] px-3 py-1.5 text-xs uppercase tracking-widest",
          "rounded-md border backdrop-blur-md transition",
          "bg-signal-cyan/10 border-signal-cyan/60 text-signal-cyan",
          "hover:bg-signal-cyan/15 hover:border-signal-cyan",
        ].join(" ")}
      >
        <span className="text-[9px] tracking-[0.2em] text-slate-400">Mode</span>
        <span className="flex-1 text-left truncate">{MODE_LABELS[mode]}</span>
        <Chevron open={open} />
      </button>

      {open && (
        <ul
          role="listbox"
          className={[
            "absolute right-0 z-20 mt-1 min-w-full w-max max-w-xs py-1",
            "rounded-md border border-white/10 bg-ink-900/70 backdrop-blur-xl",
            "shadow-xl shadow-black/50 ring-1 ring-black/30",
          ].join(" ")}
        >
          {modes.map(m => {
            const isActive = m === mode;
            return (
              <li key={m} role="option" aria-selected={isActive}>
                <button
                  type="button"
                  onClick={() => { onChange(m); setOpen(false); }}
                  className={[
                    "flex w-full items-center gap-2 px-3 py-1.5 text-xs uppercase tracking-widest text-left transition",
                    isActive
                      ? "text-signal-cyan bg-signal-cyan/10"
                      : "text-slate-300 hover:bg-white/5 hover:text-slate-100",
                  ].join(" ")}
                >
                  <span className="w-3 shrink-0 text-signal-cyan">{isActive ? "✓" : ""}</span>
                  <span className="flex-1 truncate">{MODE_LABELS[m]}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-1 h-3 text-[9px] uppercase tracking-widest text-slate-500 text-right">
        {idle ? "active on playback" : " "}
      </div>
    </div>
  );
}


/**
 * Overlay selector as a single dropdown (mirrors AoiDropdown), so the top bar
 * isn't crowded with inline toggle buttons. The trigger shows "Overlay" plus a
 * count of how many overlays are active; the menu lists each overlay as a
 * checkable row. Multiple overlays can be on at once (unlike AOI's single
 * select), so the menu stays open on toggle and rows show a ✓ when active.
 * Outside-click / Escape listeners subscribe ONLY while open.
 */
function OverlayDropdown({
  showBlocks, onToggleBlocks, showWatch, onToggleWatch, watchCount,
}: {
  showBlocks: boolean; onToggleBlocks: (v: boolean) => void;
  showWatch: boolean; onToggleWatch: (v: boolean) => void; watchCount: number;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const activeCount = (showBlocks ? 1 : 0) + (showWatch ? 1 : 0);
  const label = activeCount > 0 ? `Overlay · ${activeCount}` : "Overlay";

  const rows: { key: string; text: string; active: boolean; toggle: () => void }[] = [
    { key: "blocks", text: "Blocks", active: showBlocks, toggle: () => onToggleBlocks(!showBlocks) },
    { key: "watch", text: `Watch${watchCount ? ` · ${watchCount}` : ""}`, active: showWatch, toggle: () => onToggleWatch(!showWatch) },
  ];

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={[
          "flex items-center gap-2 min-w-[9rem] px-3 py-1.5 text-xs uppercase tracking-widest",
          "rounded-md border backdrop-blur-md transition",
          activeCount > 0
            ? "bg-signal-cyan/10 border-signal-cyan/60 text-signal-cyan hover:bg-signal-cyan/15 hover:border-signal-cyan"
            : "border-wire-700 text-slate-300 hover:bg-white/5 hover:text-slate-100",
        ].join(" ")}
      >
        <span className="flex-1 text-left truncate">{label}</span>
        <Chevron open={open} />
      </button>

      {open && (
        <ul
          role="menu"
          className={[
            "absolute right-0 z-20 mt-1 min-w-full w-max max-w-xs py-1",
            "rounded-md border border-white/10 bg-ink-900/70 backdrop-blur-xl",
            "shadow-xl shadow-black/50 ring-1 ring-black/30",
          ].join(" ")}
        >
          {rows.map(r => (
            <li key={r.key} role="menuitemcheckbox" aria-checked={r.active}>
              <button
                type="button"
                onClick={r.toggle}
                className={[
                  "flex w-full items-center gap-2 px-3 py-1.5 text-xs uppercase tracking-widest text-left transition",
                  r.active
                    ? "text-signal-cyan bg-signal-cyan/10"
                    : "text-slate-300 hover:bg-white/5 hover:text-slate-100",
                ].join(" ")}
              >
                <span className="w-3 shrink-0 text-signal-cyan">{r.active ? "✓" : ""}</span>
                <span className="flex-1 truncate">{r.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


