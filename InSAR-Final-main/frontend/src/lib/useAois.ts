import { useEffect, useRef, useState } from "react";
import { Bundle, fetchBundle } from "./bundle";
import { authHeaders } from "./telemetry";

export interface AoiSummary {
  aoi_code: string;
  name: string;
  center_lon: number;
  center_lat: number;
  side_m: number;
  phenomenon: string;
  footprint_source: string;
  narrative: string;
  bbox: [number, number, number, number];
}


/**
 * Categorized error so the UI can show the right hint.
 *  - kind="network": fetch() rejected (DNS, refused, CORS, offline). The API
 *                    is unreachable.
 *  - kind="http":    the API answered with a non-2xx status. The API IS up;
 *                    the request itself is the problem.
 *  - kind="parse":   the response arrived but couldn't be decoded (e.g. the
 *                    Int32Array alignment failure mode from a stale cache).
 */
export type ApiError =
  | { kind: "network"; message: string; cause: Error }
  | { kind: "http"; status: number; message: string }
  | { kind: "parse"; message: string; cause: Error };

function networkError(e: unknown): ApiError {
  const err = e instanceof Error ? e : new Error(String(e));
  return { kind: "network", message: err.message || "network request failed", cause: err };
}


/** Fetch the list of AOIs once. Idempotent. */
export function useAoiRegistry() {
  const [aois, setAois] = useState<AoiSummary[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    (async () => {
      try {
        const r = await fetch("/api/aois", { signal: ctl.signal, headers: authHeaders() });
        if (!r.ok) {
          setError({ kind: "http", status: r.status, message: `/aois returned HTTP ${r.status}` });
          return;
        }
        setAois(await r.json());
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setError(networkError(e));
      }
    })();
    return () => ctl.abort();
  }, []);
  return { aois, error };
}


/**
 * Bundle cache keyed by AOI code. Each bundle is fetched at most once per page
 * load; switching AOIs is then a Map lookup. Returns the currently-active
 * bundle (or `null` while loading).
 */
export function useAoiBundle(activeCode: string | null) {
  const cache = useRef(new Map<string, Bundle>());
  const inflight = useRef(new Map<string, Promise<Bundle>>());
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!activeCode) {
      setBundle(null);
      return;
    }
    const cached = cache.current.get(activeCode);
    if (cached) {
      setBundle(cached);
      return;
    }

    let cancelled = false;
    let promise = inflight.current.get(activeCode);
    if (!promise) {
      promise = fetchBundle(activeCode).then(b => {
        cache.current.set(activeCode, b);
        inflight.current.delete(activeCode);
        return b;
      });
      inflight.current.set(activeCode, promise);
    }
    promise.then(b => {
      if (!cancelled) setBundle(b);
    }).catch(e => {
      if (cancelled) return;
      // fetchBundle throws either a plain Error (HTTP non-2xx, message "bundle
      // fetch failed: <status>") or a TypeError from fetch itself. The parse
      // path also raises plain Errors (e.g. "start offset of Int32Array...").
      const msg = (e as Error).message ?? String(e);
      const httpMatch = /^bundle fetch failed: (\d+)/.exec(msg);
      if (httpMatch) {
        setError({ kind: "http", status: parseInt(httpMatch[1], 10), message: msg });
      } else if (e instanceof TypeError) {
        setError(networkError(e));
      } else if (/Int32Array|Float32Array|offset|multiple of|DataView/i.test(msg)) {
        setError({ kind: "parse", message: msg, cause: e as Error });
      } else {
        setError(networkError(e));
      }
    });
    return () => { cancelled = true; };
  }, [activeCode]);

  return { bundle, error };
}
