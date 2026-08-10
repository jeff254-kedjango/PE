/**
 * AccessGate — the login wall in front of the InSAR map.
 *
 * Renders one of three states (see lib/access.ts):
 *   checking → a brief branded splash while we verify the token with Weespas
 *   granted  → the real map (children)
 *   denied   → a "sign in to continue" card, then auto-redirect to Weespas login
 *
 * Styling matches the map shell (bg-ink-950 / slate / font-mono / signal-cyan #22d3ee)
 * so the wall feels like part of InSAR, not a bolted-on interstitial. The auto-redirect
 * gives a denied visitor one beat to read WHY before they're sent to login — friendlier
 * than an instant jump, and it keeps a manual "Sign in" button for anyone who prefers it.
 */
import { useEffect, useState } from "react";
import { verifyAccess, redirectToLogin, loginUrl, type AccessState } from "../lib/access";

// How long the "redirecting…" card lingers before the bounce, so the reason is readable.
const REDIRECT_DELAY_MS = 1600;

export function AccessGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AccessState>("checking");

  useEffect(() => {
    let alive = true;
    verifyAccess().then((s) => {
      if (alive) setState(s);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (state !== "denied") return;
    const t = window.setTimeout(redirectToLogin, REDIRECT_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [state]);

  if (state === "granted") return <>{children}</>;

  return (
    <div className="h-screen w-screen grid place-items-center bg-ink-950 text-slate-200 font-mono px-6">
      {state === "checking" ? (
        <div className="flex flex-col items-center gap-3 text-center">
          <div
            className="h-8 w-8 rounded-full border-2 border-slate-700 border-t-[#22d3ee] animate-spin"
            aria-hidden
          />
          <p className="text-sm text-slate-400">Checking your access…</p>
        </div>
      ) : (
        <div className="max-w-sm w-full flex flex-col items-center gap-5 text-center">
          <div className="text-[#22d3ee] text-3xl" aria-hidden>
            ◎
          </div>
          <div className="space-y-2">
            <h1 className="text-lg font-semibold tracking-tight text-slate-100">
              Sign in to see the risk map
            </h1>
            <p className="text-sm leading-relaxed text-slate-400">
              The subsidence risk map is free — you just need a Weespas account.
              We&rsquo;ll take you to sign in, then bring you right back here.
            </p>
          </div>
          <a
            href={loginUrl()}
            className="w-full rounded-lg bg-[#22d3ee] px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:brightness-110 active:brightness-95"
          >
            Sign in to continue
          </a>
          <p className="text-xs text-slate-600">Redirecting you to sign in…</p>
        </div>
      )}
    </div>
  );
}
