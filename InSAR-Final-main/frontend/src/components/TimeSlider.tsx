export function TimeSlider({
  n, idx, onScrub, playing, onTogglePlay, dates,
}: {
  n: number;
  idx: number;
  onScrub: (m: number) => void;
  playing: boolean;
  onTogglePlay: () => void;
  dates?: string[];
}) {
  return (
    <div className="absolute bottom-0 left-0 right-0 px-3 sm:px-6 py-3 sm:py-4 bg-gradient-to-t from-ink-950/95 via-ink-950/70 to-transparent pointer-events-none">
      <div className="pointer-events-auto flex items-center gap-2 sm:gap-4">
        <button
          onClick={onTogglePlay}
          disabled={n === 0}
          className="px-3 py-1.5 text-xs uppercase tracking-widest border border-wire-700 text-slate-300 hover:border-signal-cyan hover:text-signal-cyan transition disabled:opacity-40"
        >
          {playing ? "Pause" : "Play"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, n - 1)}
          value={idx}
          onChange={e => onScrub(parseInt(e.target.value, 10))}
          disabled={n === 0}
          className="flex-1"
        />
        <div className="text-[11px] sm:text-xs text-slate-500 w-20 sm:w-28 shrink-0 text-right tabular-nums">
          {n === 0 ? "0 / 0" : `${idx + 1} / ${n}`}
          {dates && dates[idx] && <span className="ml-1 sm:ml-2 text-slate-600 hidden sm:inline">{dates[idx].slice(0, 7)}</span>}
        </div>
      </div>
    </div>
  );
}
