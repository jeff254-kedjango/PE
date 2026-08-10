# PE — Weespas platform monorepo

Four services that share one set of rails (identity, geo, payments) to attack two
Kenyan problems: **buildings that collapse without warning**, and **small sellers who
can't get discovered**.

The through-line is proximity. InSAR knows which ground is moving; Weespas knows who
lives and sells on it. Commerce turns that same geo-graph into a marketplace where a
seller is found because they are *next door* — no following required, no cold-start tax.

> **Status: active development, MVP+.** Servers run locally on one machine across several
> ports. Nothing here is production-deployed. Documents in this repo are the source of
> truth for design decisions; those marked **(locked)** were settled in design sessions
> and should not be re-litigated without cause.

---

## The four platforms

| Directory | What it is | Stack |
|---|---|---|
| `InSAR-Final-main/` | Subsidence & collapse-risk **producer**. Reads Sentinel-1 satellite radar, scores every building, serves a read-only risk API + map UI. | Python, DuckDB, MapLibre |
| `weespas/` | The **front door**. Owns identity, roles, listings, the verification badge, and structural-flag intake. Calls InSAR; InSAR never calls it. | FastAPI, Postgres, Celery |
| `commerce/` | Neighbour **commerce** — shops, POS, inventory, social listings, proximity feed, boost/reach economy. Own database. | FastAPI, Postgres + PostGIS |
| `mobility/` | Realtime **ride dispatch** — bargain-before-dispatch, live GPS, driver state machine. Earliest stage of the four. | FastAPI, Redis |
| `weespas-frontend/` | Shared React frontend for Weespas + commerce (`/trade`, `/trade/sell`). | React, TypeScript, Vite |

Supporting directories: `dev/` (per-service local run scripts), `deploy/` (gateway config
and production env templates), `uploads/` (runtime media mount point).

### Why services, not modules

Rides and neighbour-commerce share geo + payment + identity rails and almost nothing else.
They have opposite latency, failure, and settlement profiles — sub-second matching with a
stranded rider as the failure cost, versus request/response where a stale stock count is
the failure cost. Splitting them keeps high-velocity dispatch traffic from ever being
bottlenecked by, or freezing, the heavy InSAR data pipelines.

---

## The core idea: two sensors, fused

InSAR sees the **ground move** but is blind to **construction quality** — bad concrete,
missing rebar, illegal extra floors — which is the dominant reason Nairobi buildings
collapse. So the system fuses satellite motion with a human **structural flag** recorded
by a certified engineer or authority inside Weespas:

```
engineer records flag → Weespas exports it → InSAR reads + re-scores (debounced)
                                           → building's collapse score and tier update
```

The fusion is deliberately **one-directional in risk**:

- An `UNSAFE` flag **raises** a risk floor regardless of measured motion.
- Absence of a flag **never lowers** risk.
- A `CLEARED` flag damps risk, but decays over two years and can **never** silence an
  accelerating mover.

That last constraint is the anti-corruption control: "clear the flag" is exactly where
bribery would attack, so it is the most constrained path in the system.

---

## Documentation

Read these before changing behaviour in the areas they cover.

| Document | Covers |
|---|---|
| `work_flow.md` | How the whole system runs end to end; every process, port, and local run command. **Start here.** |
| `weespas_trade_architecture.md` | The trading layer (mobility + commerce): dispatch, bargain state machine, settlement, the §8 social-marketplace requirement. |
| `commercial_model.md` | Business model and phasing. |
| `billing_architecture.md` | The no-wallet / zero-float settlement rule. |
| `SECURITY.md` | Cross-service trust boundary, data-API access control, shipped controls. |
| `analysis_one.md` … `analysis_three.md` | Design analyses feeding the above. |

---

## Engineering rules

These are enforced on every change in this repo:

1. **O(1) or better.** No N+1 queries, no unbounded scans. Batch and index.
2. **Security is paramount.** Fail-closed boot guards, no cross-tenant existence leaks,
   least-privilege scopes. Flag or fix security bugs even when out of scope.
3. **No dead code.** If it isn't reachable, it doesn't get committed.
4. **Robust error handling**, clarity, and efficiency over cleverness.
5. **Small chunks.** This codebase is large; big-bang changes introduce bugs.
6. **Perfection over speed.**
7. **Measure before theorizing.** Reproduce → capture the real failure → *then* form one
   theory. No guess-and-rerun.

### A worked example of rule 2

`POST /sellers/me/stock/bulk-csv` is all-or-nothing on *validation* — a malformed row
returns 422 naming the offending line before any listing is mutated. But listing IDs the
caller does not own are **skipped, not rejected**, and reported only as an anonymised
`skipped_count`. Raising on them would confirm "this ID exists under another seller",
turning the endpoint into a cross-tenant existence oracle. The distinction is deliberate.

---

## Security posture

- **Asymmetric stateless auth.** Weespas holds the RS256 signing keypair; commerce holds
  only the public half — it can verify weespas-minted tokens but can never forge one.
- **Fail-closed boot guard.** A service configured for production refuses to start
  without its key material. "Forgot the key" is a crash, not a silent downgrade.
- **Service-to-service** calls use a shared secret in `X-Service-Secret`, compared with
  `hmac.compare_digest`, and fail *closed* when unset.
- **Cross-service bridges fail open on outage** (degraded data, never a 5xx) but fail
  closed on misconfiguration. These are different failure classes and are treated as such.

**Never committed:** real `.env` files, `*.pem` / `*.key` material, database dumps
(`backup_*.sql`), user-uploaded media, or InSAR raster data. Only `.env.example`
templates with placeholder values are tracked — they are the documented contract.
If you clone this, copy each `.env.example` to `.env` and fill it in; see `work_flow.md`
for the required values.

---

## Running locally

Every service has a launcher in `dev/`, and `work_flow.md` documents the exact ports and
the order to start things in. Each backend has its own `requirements.txt` and expects its
own virtualenv; the frontend is a standard Vite app.

Test suites (all must be green before a change lands):

```bash
cd commerce && .venv/bin/pytest tests/     # commerce API
cd weespas  && .venv/bin/pytest tests/     # identity, listings, flags
cd weespas-frontend && npx vitest run      # React components + hooks
cd weespas-frontend && npx tsc --noEmit    # type check
```

---

## License

Proprietary. All rights reserved.
