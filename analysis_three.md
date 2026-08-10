# Weespas ↔ InSAR Integration — Design Discussion (no code yet)

_Date: 2026-06-22. Lens: distinguished software + geospatial engineer, with
senior civil-engineering and engineering-ethics review. This is a discussion
document, not a plan to execute._

---

## 0. What I hear you proposing (so we're aligned)

- **Weespas is the front door.** It owns identity, auth, roles/permissions, the
  property listings, and the verification badge. Users reach InSAR *through* it.
- **The engineer-certification badge** (agent + engineer certified safe) is meant
  to close InSAR's stated gap ("needs ground verification by engineers").
- **Listings are geolocated**, so a Weespas property should line up with an
  InSAR building footprint and inherit its risk signal.
- **Two new roles:** `professional` (civil/geospatial engineers doing analysis &
  certification) and `property-owner` (non-agent owners who monitor their own
  building's risk and get updates).
- **An escalating notification ladder** keyed to risk %, widening the audience
  (owner → +authorities → +tenants) and increasing in frequency as risk climbs —
  explicitly designed to defeat the "owner bribes official, tenants die
  uninformed" failure mode.

I get the skeleton, and the moral core is sound and serious. Below is where I
agree, where I'd push back hard as an engineer, and ideas you haven't raised.

---

## 1. The one thing that must anchor everything: **% is not probability**

This is the most important point in the whole document.

`composite_risk` is, by the project's own honest docs (`risk_model.md`):
> "a deliberately simple, explainable weighted sum. It is **not** a calibrated
> collapse probability."

So "55%" does **not** mean "55% chance of collapse." If we wire legally- and
life-consequential notifications (especially to authorities and tenants) to raw
composite %, we get danger in **both** directions:

- **False positives** → you tell tenants a building is unsafe when it isn't:
  panic, rent disputes, defamation against owners, property devaluation, and
  "cry wolf" — the next *real* alarm gets ignored.
- **False negatives** (the deadlier one) → a building unsafe from *construction
  quality* (bad cement/rebar/illegal additions — the **dominant** Nairobi
  collapse driver, which InSAR physically cannot see) reads a low %, everyone is
  reassured, and it collapses. **The system's silence gets read as "safe."**

**Recommendation:** trigger notifications off the **`danger_level` tier**
(STABLE…CRITICAL), which already exists in the codebase with **absolute mm/yr
cutoffs designed to be comparable across AOIs and robust to score retuning** —
*not* off the raw 0–1 composite. The composite % can still be *shown*, but the
*decision to notify* should ride the physically-grounded tier **plus the
confidence/defensibility gate**. A "% ladder" silently ignores the classification
gates, the abstention logic, and the uncertainty band the system already computes.

Corollary: **abstention must never read as safety.** ~25% of buildings are
`INSUFFICIENT_EVIDENCE`. Their notification state is "we cannot assess — get an
inspection," never silence.

---

## 2. The real product isn't the alert — it's the **un-erasable record**

Your anti-corruption insight is the best part of this, and I want to sharpen it.
Telling the tenant is the circuit-breaker, yes. But the deeper mechanism that
defeats "the owner bribed the official" is **a tamper-evident audit trail that
proves who was told what, when.**

Make every notification an immutable, hash-chained log entry (each record
includes the hash of the previous one, so no row can be altered or deleted after
the fact without detection). Then:

- An official who ignored a 55% alert can't later claim "I was never informed" —
  the chain proves the SMS/email was dispatched on date X.
- A tenant who died can't have their family told "no one knew" — the record
  stands.
- The log itself becomes admissible-quality evidence and a public-accountability
  artifact.

**This audit chain is arguably the thing worth building first**, before any
fancy delivery logic. The notification is ephemeral; the *proof* is the product.

Pair it with an **acknowledgement loop**: high-tier alerts require the recipient
to ACK. An un-acked high-tier alert **auto-escalates** (to a higher authority, or
to a public registry / watchdog). That defeats both "I didn't get it" and "I got
it and sat on it."

---

## 3. Architecture: a clean producer/consumer split

Don't merge the two systems. Keep their strengths separate:

- **InSAR = risk producer.** It owns the data, the scoring, the ~12-day refresh
  cycle (now Celery-orchestrated), and — critically — **threshold-crossing
  detection**. When a building's tier escalates (with hysteresis, see §5), the
  pipeline emits a **risk event** for that `building_id`. This fits cleanly:
  detection happens in the *build/refresh* path, which now has Celery — **never
  on the read hot path** (the O(1) bundle serving stays untouched, per our locked
  rule).
- **Weespas = identity + delivery.** It knows who the owner/tenant/authority is,
  it has the user records, and it already has Africa's Talking SMS + the
  notification surfaces. It consumes InSAR risk events, resolves recipients, and
  delivers + logs them.

```
InSAR refresh (Celery) ──emits risk_event{building_id, old_tier, new_tier, conf}
        │
        ▼
Weespas: map building_id → property/owner/tenant/authority subscriptions
        → apply ladder + hysteresis → deliver (SMS/email/push) → append to audit chain
```

Why this split and not "Weespas calls InSAR per request": risk changes on a
12-day cadence, not per request. Event-on-change is far cheaper and is the
natural shape. For the *read* side (a user opening a listing), Weespas calls a
cached InSAR **risk-at-point** endpoint (Phase 4 from `analysis_two.md`), Redis-
cached by `(aoi, geohash, bundle_etag)` — that's the live "show me the badge"
path, separate from the event path.

> Both projects are FastAPI + (now) Celery + Redis with the **same versions** —
> the integration rides infrastructure that already exists on both sides.

---

## 4. Roles are **relationships to buildings**, not just flags

`professional` and `property-owner` are both good additions. But notice they
aren't really new *permission levels* on the existing flat ladder
(user/agent/staff/admin) — they're **relationships between a person and a
specific building**:

- `professional` (civil/geo engineer): can **certify** a building, and gets
  deeper read access (uncertainty bands, time series, the diagnostic columns —
  `closure_rms`, `dem_err`, tilt). They are the ground-truth providers.
- `property-owner`: **owns** building(s) → monitors them, receives owner-tier
  alerts. Requires a **claim-and-verify** step (else anyone subscribes to anyone's
  building → privacy + notification abuse).
- `tenant` (your "user who is renting"): **rents** a unit in a building →
  receives tenant-tier alerts. Needs a link to the building (ideally captured
  *at rental time through the marketplace* — see §7).
- `authority`: responsible **for an area/jurisdiction**, not a building.

So I'd model this as: keep `professional` as a role flag (it's a capability), but
add a **`building_subscription` / relationship table** —
`(user_id, building_ref, relation ∈ {owner, tenant, certifier, authority}, verified_at)`.
The notification ladder then resolves an audience by *querying relationships for a
building*, which is clean, auditable, and privacy-respecting. This also means
"who are the tenants?" stops being a mystery — they're rows you captured when they
rented.

`property_owner` likely belongs in the multi-role `user_roles` table you already
have (a person can be both a `user` and a `property_owner`), which the codebase
already supports.

---

## 5. The notification ladder — reframed

Your ladder's *intent* is excellent: widen the audience and raise the frequency
as risk climbs. Two mechanical fixes make it safe and implementable.

**(a) Trigger on tier escalation + confidence + hysteresis — not raw %.**
InSAR velocity is noisy; a building oscillating around a % threshold would spam
authorities every cycle. So:
- Map your bands onto the absolute `danger_level` tiers (+ a sub-tier from the
  score if you want finer steps), gated by the **defensibility/confidence** check.
- **Hysteresis + sustain:** only escalate when the higher tier is crossed *and
  held for ≥N acquisitions* (or confirmed by the trend/acceleration signal, which
  the model already separates from noise). De-escalate only after a longer
  sustained drop. This kills alert-spam from noise.
- **Notify on ESCALATION events, not on every recompute.** Re-notify at a
  *cadence that increases with tier* (your frequency insight) — e.g. monthly at
  low tiers, weekly then daily as it climbs — rather than re-sending identically.

**(b) Audience expands monotonically and never contracts.** Once authorities are
informed, they stay informed even if the reading dips (a dip might be seasonal
clay swell, not improvement — the model's STL elastic/plastic split matters
here). Roughly:

| Tier (grounded, not raw %) | Audience | Cadence | Requires ACK |
|---|---|---|---|
| Elevated | owner, certifier | low | no |
| High | + (owner repeated) | medium | owner |
| Severe | **+ authorities** | high | owner, authority |
| Critical | **+ tenants** | highest, recurring | all |
| Insufficient evidence | owner only: "cannot assess, inspect" | once | — |

This preserves your escalation spirit while (i) routing on physics not an
uncalibrated %, (ii) not crying wolf, (iii) making the tenant the circuit-breaker
at the top, and (iv) logging every step immutably.

**(c) Language discipline (civil + legal).** Every message says *what was
measured*, never a verdict: "Elevated ground-movement signal detected at
<building>; independent structural inspection recommended" — **not** "your
building is unsafe." This is both honest (it's a screening signal) and your
defamation/liability shield. The existing disclaimer must travel with every alert.

---

## 6. The verification badge — it closes the gap *in the time dimension*

You're right that engineer-certification answers InSAR's "needs ground
verification." But the sharper truth makes the integration far more valuable:

**Certification is a snapshot; InSAR is the movie.** An engineer certifies a
building safe *today*. InSAR then watches it for the next 18 months. So the
highest-value alert in the whole system is **"certified safe on date X — now
moving."** Someone vouched for it, and it's changing.

This creates two features you didn't list:

1. **Certification decay / re-cert prompts.** A badge isn't forever; pair it with
   "monitored since" and auto-prompt re-certification when InSAR detects drift.
2. **Certifier accountability loop.** If a `professional` certified a building and
   InSAR later shows it degrading, surface that — both as a high-value alert and
   as an *integrity check on the certifier*. This **deters fraudulent
   certification**, which is the same corruption problem as the bribed official,
   one layer up. (Handle fairly: movement ≠ bad cert; it may be new ground
   conditions. But the pattern across a certifier's portfolio is signal.)

---

## 7. Footprint concurrence — the geospatial honesty problem

You expect Weespas listings to "concur with" InSAR footprints. Here's the
reality from the code, and it needs explicit handling:

- **Weespas has points** (`Address.latitude/longitude`); **InSAR has footprint
  polygons** (Open Buildings ML / OSM). The link is *point → footprint* via
  point-in-polygon, falling back to nearest-footprint within a small radius.
- **Coverage is only 5 neighborhoods.** Most Weespas listings (Nairobi/Mombasa/
  Kisumu) fall **outside** any InSAR AOI. The badge must then read **"Not
  monitored — outside coverage,"** never "safe." Absence ≠ safety (this is the §1
  false-negative trap again).
- **Many listings → one footprint.** Twenty apartments in one block share one
  building footprint, and `insar_pixel_share` already records that a single InSAR
  pixel may cover several structures. So one reading legitimately backs many
  listings — but the UI must say "shared estimate across the block," not imply
  per-unit precision.
- **Geocoding imprecision.** A lat/lon may land just outside the true footprint.
  Store the resolved mapping as an explicit `(listing → building_id, match_method,
  match_confidence)` record so it's auditable and correctable, not a silent
  nearest-neighbor guess.

**Three-state coverage badge, always:** `Monitored: stable` / `Monitored:
elevated` / `Not monitored`. Never collapse the third into the first.

---

## 8. New ideas you haven't raised

1. **Immutable audit chain as the headline product** (§2) — the anti-corruption
   core is *proof of notification*, not the notification.
2. **Acknowledgement + auto-escalation** (§2) — un-acked high-tier alerts climb to
   a higher authority / public registry.
3. **Velocity-of-risk, not just level.** A building going 10%→40% in one cycle is
   scarier than one sitting at 50% for two years. Alert on *rate of change*
   independent of absolute tier — the pipeline already computes acceleration and
   trend.
4. **Differential settlement (tilt) as a distinct, louder trigger.** Uniform
   settling rarely cracks a frame; *angular distortion* does (and it's already an
   escalate-only term in InSAR). A tilt escalation should outrank an equal
   subsidence one in the ladder.
5. **Tenant capture at rental time.** Solve "who are the tenants?" natively: when
   someone rents through Weespas, auto-offer a monitoring subscription. The
   marketplace flow *is* the tenant registry.
6. **Risk data is sensitive / PII-adjacent.** A building's risk affects its value.
   Access control: owner sees full detail for *their* building; the public sees
   only the coarse coverage badge; authorities see their jurisdiction;
   professionals see depth. Don't expose fine-grained risk of an arbitrary
   building to an arbitrary user.
7. **Quiet-period / consent for authority+tenant tiers.** Going to authorities and
   tenants is a real-world act with legal weight — it should be gated behind a
   formal recipient registry (real NCA/county contacts, an MOU) and explicit
   product policy, not auto-fired from a synthetic-data dev build.

---

## 9. Hard constraints & a sane rollout order

Both products are **mostly synthetic in dev today** (you confirmed this for
both). That's fine for building the *plumbing*, but it bounds what may go *live*:

1. **Calibration debt gates the dangerous tiers.** `risk_model.md` has a
   calibration plan (geocode historical NCA/news collapses → tune thresholds to
   AUC). Until that's done on **real** data, **owner + professional**
   notifications are fine (consensual, opt-in, low blast radius), but
   **authority + tenant** notifications are premature — they need calibrated
   thresholds + a legal/recipient framework. Build the machinery now; gate the
   high tiers behind calibration.
2. **Synthetic data must never trigger a real-world notification.** The same
   discipline as the seeder guard: a notification may only fire for a building
   whose provenance is `insar` (real). Synthetic/`partial` buildings can exercise
   the pipeline end-to-end in a **dry-run / sandbox** mode that logs "would have
   notified" but sends nothing externally. This mirrors the `--force`/`insar`
   guard we just built.
3. **Legal/consent before authority+tenant.** Who exactly are "authorities" (NCA?
   county disaster mgmt?)? Real contacts + a data-sharing agreement are
   prerequisites, not code.

**Suggested phasing:**
- **P4a — Identity & mapping (safe now):** add `professional` + `property_owner`
  roles, the building-relationship/subscription table, and the
  listing→footprint resolver with the 3-state coverage badge. No notifications
  yet. All synthetic-safe.
- **P4b — Read integration:** InSAR risk-at-point API + Redis cache; Weespas shows
  the coverage/risk badge on listings, gated by access control.
- **P4c — Event + audit spine:** InSAR emits risk events on tier escalation
  (hysteresis); Weespas resolves recipients and writes the **immutable audit
  chain**; **owner/professional** notifications go live in dry-run, then real for
  `insar` buildings.
- **P4d — High tiers (gated):** authority + tenant notifications + ACK/escalation,
  unlocked only after calibration + legal framework.

---

## 10. Open questions for you (these change the design)

1. **Trigger basis:** OK to drive notifications off the physically-grounded
   `danger_level` tier + confidence (my strong recommendation), rather than raw
   composite %? The % becomes a display, not the trigger.
2. **"Authorities":** who concretely? (NCA, Nairobi County disaster mgmt, …) — and
   is a real recipient registry + agreement in scope, or do we build the machinery
   and leave the authority tier disabled until that exists?
3. **Scope of this round:** build P4a (identity + mapping + coverage badge, fully
   synthetic-safe) first, or go straight for the event/audit spine (P4c)?
4. **Build target vs design:** do you want me to start implementing P4a now, or
   keep refining this design (and capture decisions) before any code?

---

## 11. Decisions locked (2026-06-22) + corrections

**Naming.** The "composite risk" we keep referring to is the function
`composite_risk()` in `scripts/postprocess.py` — its docstring now reads
*"Continuous [0,1] COLLAPSE SCORE"*. It is the movement-dominant collapse score,
**not** a calibrated probability. `danger_level()` is the separate ABSOLUTE tier
(STABLE…CRITICAL) with fixed mm/yr cutoffs, built to be comparable across AOIs and
robust to score retuning. **Notifications fire off `danger_level` (the tier),
the % is display-only.** (Open Q1 → resolved: tier, not %.)

**The 85% / "one tremor and we're doomed" reconciliation.** This does NOT
contradict the construction-quality point — it *is* the case for the integration.
InSAR sees motion; it is blind to construction quality (the dominant collapse
driver), so InSAR alone would miss most of the unsafe 85%. The engineer/authority
structural flag is the **second, orthogonal sensor** that sees what InSAR cannot.
Fusing them is what makes the score track the real collapse population. (Memory
already records enforcement notices fired 3× before the South C collapse — signal
InSAR never had.)

**Fusion asymmetry (locked).** A flag may *amplify-mostly*:
- UNSAFE / AUTH_UNSAFE raise a danger FLOOR regardless of InSAR motion.
- Absence of a flag NEVER lowers risk (un-inspected ≠ sound).
- A CLEARED flag may damp risk but is **bounded, age-DECAYING (Open Q1/clearance →
  decay locked), and cancelled by motion** — and can never suppress a PLASTIC /
  hard-accel mover (the protect floor re-fires after the damp). The corruption
  we're fighting attacks the *clearance* path, so that's the path made weakest.

**Flag intake.** Manual entry now (a professional/authority records a flag via
Weespas), with the table + loader shaped so an automatic NCA/enforcement feed
plugs into the same seam later. (Open Q2 → "authorities" concrete identity still
deferred; the authority *tier of notifications* stays gated behind a real recipient
registry + legal agreement, but the authority *role* and AUTH_UNSAFE *flag* are
built now.)

**Scope (Open Q3/Q4 → resolved).** Build P4a now, both repos, in one landing, with
the scoring fusion shipping **inert** (defaults to no-flag ⇒ byte-identical scores).

---

## 12. Architecture: databases, scaling, defensive measures

**How many databases — two engines, three roles, resist a third.**
- **PostgreSQL = system of record** (Weespas): identity, roles, listings, the new
  `building_link` + `structural_flag` tables, and the `notification_audit` chain.
  The audit chain needs ACID + referential integrity — that's Postgres's job. This
  is the one place to ADD tables, not engines.
- **DuckDB = analytical read replica** (InSAR): the served dataset is ~268 KB — it
  fits in L2 cache. Keep it. The atomic-file-swap rebuild is a free lock-free
  blue/green data deploy; Postgres would only add a server to operate for no gain.
  Do NOT migrate InSAR into Postgres.
- **Redis = ephemeral only**: index /0 Weespas cache, /1 Weespas Celery, /2 InSAR
  pipeline broker, add **/3** for the P4b risk-at-point read-cache. Never a system
  of record.
- **Refuse a shared "integration DB."** Postgres owns truth, DuckDB owns analytics,
  they talk over a versioned HTTP contract (the risk-at-point API) with Redis
  caching. A shared store would couple two independently-deployable systems into
  one failure domain. The engineer/authority flags are write-of-record →
  Postgres; they flow INTO InSAR at build time as an input layer (like soil class),
  never computed on the read path.

**Load balancing — opposite axes.**
- InSAR read serving is stateless/read-only over an in-RAM bundle → scale by N
  identical replicas behind a round-robin LB (each holds its own 268 KB copy; no
  shared state, no sticky sessions). The rare service where horizontal scaling is
  trivially correct; the ETag/304 path lets the LB cache too.
- Weespas is the stateful one → app workers + one Postgres primary + read replicas
  for heavy reads, PgBouncer to cap connections. **Audit-chain writes go to the
  primary** (hash-chain ordering matters).
- The pipeline scales by Celery `--concurrency`, bursty (~12-day), one host suffices.

**Defensive measures.**
- *Run-out/timeouts*: per-task `time_limit`/`soft_time_limit`; `task_acks_late`
  re-queues a killed task; DuckDB statement timeout on reads; SMS send deadline →
  `undelivered` audit row + auto-escalate.
- *Bug blast-radius*: notifications dry-run by default; real send gated on BOTH
  `provenance == insar` AND an explicit `NOTIFY_LIVE` flag (two locks, like the
  seeder `--force`); SMS-provider circuit breaker; hash-chain self-detects
  tampering/corruption.
- *Rate limits*: inbound per-token bucket on the risk API (a Weespas bug can't DoS
  InSAR); outbound SMS deduped via the ladder's hysteresis + per-tier cadence;
  upstream HyP3/GACOS/OpenSARLab quotas respected by existing backoff +
  `worker_prefetch_multiplier=1`.
- *Maintenance*: DuckDB atomic swap = zero-downtime data deploys; **Alembic is now
  in place** (P4a) so the audit table — legal evidence — is never hand-edited.
- *Space*: served data O(buildings), tiny + constant; growth is in time-series +
  the audit chain (both O(time)). Audit chain is append-only, month-partitioned,
  archived-not-pruned (it's evidence). Parquet partition-by-AOI ⇒ adding AOIs is
  O(1) on existing data.
- *Idempotency*: pipeline tasks already idempotent; notifications keyed by
  `(building_id, tier, escalation_epoch)` + the `row_hash` unique constraint so a
  retried task can't double-send.

---

## 13. Implementation status — P4a SHIPPED (2026-06-22)

**InSAR side (fusion ships inert — verified byte-identical to pre-change):**
- `scripts/postprocess.py`: `STRUCT_*` constants; `composite_risk()` gains a
  clearance damp (decay × motion-override, before the protect floor) + an
  unsafe/auth-unsafe floor (after it); `danger_level()` escalates on unsafe flags,
  never lowers on clearance; 3 nullable `BUILDINGS_SCHEMA` columns.
- `scripts/structural_flags.py` (new): `fetch_structural_flags()` loader reading an
  optional `data/structural_flags/<aoi>.json` export; absent/malformed ⇒ all
  STRUCT_NONE (fail-safe; never auto-clears).
- `scripts/join_insar.py` + `scripts/phenomena.py`: thread flags through; populate
  the new columns.
- `tests/test_structural_flag_fusion.py` (new, 46 tests) incl. the load-bearing
  "fresh clearance cannot hide a PLASTIC mover" test.
- **Verified:** re-seed → `composite_risk` AND `danger_level` SHA-identical to a
  golden across all 5 AOIs (55,800 env rows / 6,200 buildings). Full InSAR suite
  **122 passed**. Serving `app/main.py` untouched.

**Weespas side:**
- Alembic introduced non-destructively: `migrations/env.py` with an
  `include_object` allow-list (only the 3 new tables) + baseline `stamp`. Verified
  autogenerate emitted **0 DROPs**, the 17 legacy tables are untouched.
- `models/insar_link.py` (new): `building_link`, `structural_flag`,
  `notification_audit` (hash-chained). Migration adds an **append-only trigger +
  REVOKE** on `notification_audit` — verified it rejects UPDATE/DELETE, allows
  INSERT.
- New roles `professional` / `property_owner` / `tenant` / `authority` (granted via
  the VARCHAR `user_roles` table only — no PG enum ALTER); `require_certifier` gate.
- `services/structural_flag_service.py` + `routers/structural_flags.py`: manual
  flag entry (only an authority may set AUTH_UNSAFE).
- `services/insar_resolver.py`: listing→footprint resolver, 3-state
  (monitored / not_monitored / unavailable — never collapses unknown→safe), persists
  `building_link`. Verified end-to-end against the real InSAR DuckDB.
- Test suite unified onto the `PE.weespas.*` import root; **36 passed** (18 new).

**Sync loop (manual entry → InSAR build), shipped 2026-06-22:**
- `services/structural_flag_export.py` (Weespas): exports the `structural_flag` table
  to `<insar_flags_export_dir>/<aoi>.json` in the exact format
  `fetch_structural_flags()` reads. Latest-JUDGEMENT-wins (by `observed_at`, then
  `created_at`), atomic temp+rename write, FLAG_NONE skipped. Auto-fires (best-effort)
  after a flag is recorded; explicit `POST /structural-flags/export` (staff) for re-sync.
- `scripts/phenomena.py` (InSAR synthetic path) now also reads flags via
  `fetch_structural_flags`, so the loop is LIVE on today's all-synthetic data — a flag
  recorded in Weespas affects the synthetic build too. Inert (all-NONE) with no export.
- `scripts/init_db.sql`: the `buildings` view now selects the 3 flag columns.
- **Verified end-to-end:** record UNSAFE on huruma/100000 → export → re-seed → that
  building escalates STABLE→HIGH, composite_risk = 0.85 (the UNSAFE floor); unflagged
  neighbours unchanged. Removing the export + re-seed restores the danger SHA to the
  original golden exactly (no-flag = byte-identical). Tests: +5 exporter (Weespas 41
  total), InSAR still 122. Clean all-synthetic baseline restored after the test.

**Debounced auto-rebuild (close the loop end-to-end), shipped 2026-06-22:**
Recording a flag exports the JSON *and* asks the InSAR pipeline to re-score that AOI,
so the operator no longer has to run a rebuild by hand. Trailing-edge debounced so a
burst of flags coalesces into ONE rebuild.
- `scripts/pipeline/tasks.py` (InSAR): `request_rebuild` → bumps a per-AOI Redis token
  (`incr`) and schedules `_debounced_rebuild` after `INSAR_REBUILD_DEBOUNCE_SECONDS`
  (default 120s); the deferred run executes only if its token is still latest (else a
  newer request superseded it → no-op). No Redis ⇒ degrades to run-now (correctness
  over coalescing). `rebuild_aoi` is **provenance-aware**: `insar` AOIs re-JOIN
  (gate→join — the seeder refuses real data, so re-seeding would silently drop the
  flag); `synthetic`/`partial` AOIs re-SEED via `scripts/reseed_one.py`.
- `scripts/pipeline/control_api.py` (InSAR): `POST /admin/request-rebuild` (same
  fail-closed `X-Admin-Token` auth as `/admin/refresh`) → `tasks.request_rebuild.delay`.
- `services/structural_flag_export.py` (Weespas): `trigger_rebuild(aoi)` — best-effort
  POST to the control API (urllib, short timeout); **no-op when URL/token unset**, and
  **never raises** (a recorded flag must not fail because InSAR is down — failure is
  logged, operator/scheduled rebuild picks it up). Wired into `create_flag` AFTER a
  successful export (only triggers once the file is on disk).
- Config: `insar_control_api_url`, `insar_admin_token`, `insar_control_timeout_s`
  (all in `.env.example`); `INSAR_REBUILD_DEBOUNCE_SECONDS` on the InSAR side.
- Tests: +9 InSAR (debounce token coalescing, stale-token skip, no-Redis fallback,
  provenance dispatch both ways, endpoint auth 503/401/enqueue) → **InSAR 131**;
  +5 Weespas (`trigger_rebuild` disabled-without-url/token, POST shape, network-error
  swallowed, non-2xx=False) → **Weespas 46**. Both full suites green; `app/main.py`
  untouched; no export dir / no flags left on disk.

**Deferred (unchanged):** P4b risk-at-point API + Redis cache; P4c notification
delivery + audit population + ACK/escalation; P4d authority/tenant tiers behind
calibration + legal recipient registry.
