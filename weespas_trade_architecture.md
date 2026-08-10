# weespas_trade_architecture.md — the trading layer (mobility + commerce)

> **Scope.** Architecture for the third pillar — the **trading layer** (working name
> "weespas-trade", to be renamed). It covers two new services (**mobility** and
> **commerce**), how they reuse the existing identity/geo/payment rails, the
> realtime dispatch design, the bargain state machine, and the float-free 3 %
> settlement model. It is written in the same house style as `work_flow.md` and
> `analysis_three.md`, and it inherits the locked decisions in
> `commercial_model.md` §9 (Marketplace is Phase 2, same rails) and the no-wallet
> / zero-float rule in `billing_architecture.md`.
>
> _Date: 2026-06-26. Lens: lead engineer. Decisions marked **(locked)** were
> agreed in the 2026-06-26/27 design sessions; everything else is proposed and open._
>
> **Build status (2026-06-27).** Commerce increment 1 SHIPPED: the `PE/commerce/`
> service (own DB, PostGIS+dual-path testing, RS256 public-key verify, fail-closed
> boot guard) + the proximity feed slice (§5/§8 ranking) + the weespas↔commerce
> token bridge. Tests green: commerce 15 (+1 opt-in PostGIS skipped), weespas
> 211→216, InSAR 165 untouched. Deferred per plan: POS/inventory, receipts,
> delivery dispatch, the radiating-blocks map UI, settlement, KYC, mobility. NOT
> committed. See `.claude/plans/vast-leaping-pinwheel.md`.

---

## 0. The one-paragraph mental model

Weespas already owns a **front door** (identity, roles, M-Pesa, a proximity-native
geo search) and a **CAC engine** (InSAR's free risk map pulls traffic). The trading
layer rides those same rails to attack two Kenyan pain points: (1) **ride-hailing**
— riders resent that Bolt keeps a cut and adds bureaucracy, so we take only **3 %**,
settle **at the end of the ride with no strings**, and let price be **bargained**
the way Kenyans actually transact; and (2) **neighbour commerce** — normal social
media taxes every small seller with a cold-start problem (no following → no sales),
whereas our **geo-radius graph is proximity-and-intent-native**: *get discovered
instantly by the people next door, no following required — just sell.* The trading
layer is **two services sharing rails, not one module**: a realtime **mobility**
service (rides) and a **commerce** service (shops, POS, neighbour selling), because
they share identity + geo + payment but have opposite latency, failure, and
settlement profiles.

---

## 1. Why a separate layer, and why two services inside it (locked)

**Decoupled from listings/InSAR (locked).** The trading layer gets its **own
database**. High-velocity transaction/bidding/dispatch traffic must never bottleneck
— or be frozen by — the heavy InSAR data pipelines, and vice-versa. This mirrors the
existing split: DuckDB suits InSAR's RAM-precomputed read-only analytics; Postgres
suits Weespas's transactional identity; the trading layer is a new project free to
pick the best tool for realtime trade.

**Two services, not one (locked).** Rides and neighbour-commerce share *geo +
payment + identity rails* and almost nothing else:

| | **mobility** (rides) | **commerce** (shops + neighbour selling) |
|---|---|---|
| Core engine | realtime dispatch, live GPS, driver state machine, bargain-before-dispatch | inventory CRUD, stock reorder, digital receipts, social listings |
| Latency profile | sub-second matching, push channel, ephemeral positions | request/response; eventual consistency is fine |
| Failure cost | rider stranded, money mid-flight | stale stock count |
| Settlement | party-direct + 3 %, fares > 100 KES | party-direct + 3 %, micro-sales ≤ 100 KES (must stay Kadogo-free) |

They cannot even share a settlement *mechanism* (see §6), which is the clearest
proof they should be separate services. They share a **DB cluster and brand**, not a
transaction model.

```
                 weespas-listings  (identity owner — the front door)
                   │  mints RS256 JWT: { sub, role, scope[], property_uuid }
                   ▼  (public key copied to both services — verify in-memory, 0 network calls)
        ┌──────────┴───────────┐
   mobility svc            commerce svc
   own DB (PostGIS)        own DB (PostGIS)
   - ride state machine    - shop admin / POS (stock, reorder)
   - bargain handshake     - inventory + digital receipts
   - dispatch / geo-match   - social/neighbour listings (social-media feel)
   - locked-price ledger   - proximity discovery
        └──────────┬───────────┘
   shared:  Redis GEO (live drivers) · Redis Pub/Sub (events) · M-Pesa rail · RS256 identity
            geo proximity graph  ← the moat (users + sellers + riders in one index)
```

---

## 2. Authentication & permissions — asymmetric stateless (locked, already proven)

**The pitfall avoided.** The naïve plan was to have the trading layer call an
internal validation endpoint on weespas-listings for every action — a *distributed
monolith* that adds 100 ms+ of network latency to every trade and couples uptime.

**The fix (locked): asymmetric stateless auth (RS256 JWT).** When a user logs into
listings they receive a cryptographically **signed** token carrying their identity
and trade scopes (e.g. `"scope": ["create:trades", "create:rides"]`). Each trading
service keeps a **copy of the public key** and verifies *who the user is and what
they may do* completely **in-memory** — internal network calls to zero.

> **This is not aspirational — we already run it.** Weespas mints RS256 tokens for
> the InSAR telemetry bridge today (`weespas/core/config.py:197`,
> `weespas/services/auth_service.py:65`). Access tokens stay HS256; only
> cross-service tokens go RS256. The trading layer reuses this exact pattern: it is
> a *new audience* (`aud`) on the same asymmetric-trust design.

**The money caveat — stateless tokens can't be revoked, so gate settlement (locked).**
A signed token is valid until it expires; it cannot be cancelled mid-session. That is
fine for reads and identity (the O(1) in-memory fast path). It is **not** fine for a
rider caught defrauding mid-ride, a ban, or a chargeback. So:

- **Reads / identity** → stateless RS256 verify only. O(1), no I/O.
- **Money-moving / dispatch actions** → additionally check a small **revocation
  denylist** (`jti` or `user_id`) in Redis. An `SISMEMBER` is **O(1)**. A banned
  actor is stopped **now**, not at token TTL.

Keep the fast path stateless; make *settlement* revocable. Same discipline already
lives in the codebase as the telemetry-scope reject in `auth_service.py`.

---

## 3. Data syncing & frontend stitching — synchronized UUIDs (locked, already proven)

Because the trading layer owns its DB, you **cannot** SQL-JOIN a marketplace item to
a property's InSAR structural profile. **The fix (locked):** carry robust
**synchronized UUIDs** (`property_uuid`) across all databases. The frontend issues
**concurrent** API calls to the trading service and to InSAR and **stitches on the
client** — trade data + the structural **Confirmed** safety badge rendered together.

> **Already proven in this codebase.** The Confirmed-shield batch endpoint
> (`/insar/listings/confirmed`) does exactly this — a no-N+1 batch lookup keyed by
> id, stitched client-side, **no cross-DB join**. The trading layer reuses the
> pattern verbatim.

**One caution: decouple the *transactions*, not the *geo+identity graph*.** The moat
is proximity matching. Users, sellers, and live riders must live in **one** geo index
or the "person next door" discovery evaporates. Over-decoupling kills the
differentiator. Split the transaction tables; keep the proximity graph unified.

---

## 4. Geo index — PostGIS for the durable graph, Redis GEO for live drivers (locked)

**PostGIS is the source of truth (locked).** A new DB on a new project is free to
pick the best tool, and for *"who is within radius R of this point, right now"*
PostGIS `geography(Point)` + a **GiST** index is the correct, boring, proven choice.
`ST_DWithin` on a GiST index is ~**O(log n + k)** (k = hits in radius) — the
index-backed lower bound for spatial range queries, and what every mapping product
uses. This holds the **moat data**: shops, sellers, social listings, property
anchors.

**But split the two geo workloads — they have opposite access patterns:**

| Workload | Mutation rate | Store | Why |
|---|---|---|---|
| **Static proximity** (shops, sellers, listings, property anchors) | rare | **PostGIS** `geography` + GiST | durable, transactional, source of truth |
| **Live driver positions** (boda moving every 3–5 s) | thousands/min, ephemeral | **Redis GEO** (`GEOADD`/`GEOSEARCH`) | in-memory geohash sorted-set, sub-ms radius read; already running |

**Why not put live GPS in PostGIS:** every 3-second ping re-writes a GiST entry and,
under Postgres MVCC, leaves a dead tuple → **index write-amplification + constant
VACUUM** → death by a thousand cuts at scale. Redis GEO is purpose-built for it, and
driver positions are *ephemeral by nature* — if Redis loses them, the next ping (3 s
later) repopulates. **Redis is already running** (`redis_url` db 0, Celery db 1), so
this layer is free.

> **Driver-layer decision (locked): the mobility service uses `asyncpg` +
> GeoAlchemy2, not sync `psycopg2`.** The current Weespas stack is sync
> `psycopg2-binary`. Under a realtime dispatch path, a blocking driver pins worker
> threads during the spatial read. The trading DB is greenfield (no migration cost),
> so it adopts async from day one. Mixing sync psycopg2 into a realtime service is a
> latency footgun we name now so it's a decision, not an accident.

---

## 5. Dispatch / realtime — SSE downlink + HTTP uplink + Redis Pub/Sub (locked)

**The wrong default is WebSocket.** Dispatch is **asymmetric** by direction:

| Channel | Direction | Frequency | Transport |
|---|---|---|---|
| Driver → server GPS pings | uplink | every 3–5 s | **plain HTTP POST** (fire-and-forget) |
| Server → rider/driver events ("assigned", "2 min away", "countered") | downlink | event-driven, bursty | **SSE** (server-sent events) |
| Rider ↔ driver bargain | bidirectional-ish | a few msgs, seconds apart | **POST to act + SSE to hear** |

**Almost nothing here needs true bidirectional WebSocket.** Even the bargain is
"POST your offer, listen on SSE for the counter." WebSocket would only add
connection-state management, sticky-session load-balancing pain, harder handshake
auth, and a heavier reconnect story — all **worse** on flaky Kenyan mobile networks.

**SSE wins on exactly our constraints:**
- **Auto-reconnect is built into the EventSource spec** with `Last-Event-ID` replay
  — a boda through a dead zone reconnects and replays missed events for free.
- Rides plain HTTP/2 — through every proxy, no upgrade negotiation.
- Auth is a normal `Authorization` header on the GET — our **RS256 verify slots
  straight in**.
- One-directional downlink = exactly the "server tells client what happened" shape.

**Backbone: Redis Pub/Sub (already running).**

```
Driver app  --POST /ping (lat,lng, every 3s)-->  mobility svc  --GEOADD-->  Redis GEO
                                                       │
Rider --POST /rides--> matcher: GEOSEARCH Redis for N nearest drivers
                                                       │
                                          PUBLISH ride-events:<driver_id>
                                                       │
Driver: GET /events (SSE) <--SUBSCRIBE ride-events:<driver_id>-- Redis Pub/Sub
Rider:  GET /events (SSE) <--SUBSCRIBE ride-events:<rider_id>--  Redis Pub/Sub
```

Each client holds one SSE GET subscribed to its **own** channel
(`ride-events:<user_id>`). Matcher/bargain logic just `PUBLISH`es; whichever app
instance holds that user's SSE connection relays it down. **Horizontally scalable
for free** — any instance can publish, any can hold the connection, Redis is the
shared bus, **no sticky sessions**.

**Phasing:**
- **MVP (phase 1):** SSE downlink + HTTP uplink + Redis Pub/Sub. Ships on infra we
  already have. Covers dispatch, bargain, "driver arriving."
- **Phase 2 (locked):** **live driver-dot map tracking** at 1 s granularity is
  Phase 2 — that single feature can add a faster SSE tick or a WebSocket then. We do
  **not** pay for it now.

> **Deploy note.** SSE needs the ASGI server not to buffer and to allow many
> concurrent open connections. Uvicorn is fine; if Nginx fronts it (see `PE/deploy/`)
> the SSE route needs `proxy_buffering off`. Flagged so it isn't a surprise.

---

## 6. Settlement — float-free, party-direct + a separate 3 % (locked)

**The constraint our model bans:** holding a wallet / float (`commercial_model.md`
§11, `billing_architecture.md`). "Take 3 %, driver keeps the rest, settle at the end
of the ride, no strings" must be delivered **without us custodying the fare**.

**Preferred (locked): Option B — party-direct + separate 3 %.**
- The **driver/seller's own** M-Pesa (MSISDN/till) is the STK recipient. The
  rider/buyer pays them **directly, 100 %** of the locked price. **No float touches
  us.**
- We collect our **3 %** on a **second rail** (post-action STK or an accrued tab the
  party authorizes).
- **"Atomic" = a guarantee in *our ledger*, not on the M-Pesa rail.** We record the
  obligation atomically against the locked price; the rail settles in two events.

**Fallback (locked policy): if Daraja can't do party-direct, minimize hold time —
never park money.** Research may show M-Pesa won't cleanly pay the party direct while
we skim 3 %. If so we fall back to collect-then-disburse, **but bounded by one rule:
hold for the shortest possible time — seconds, not days.** The instant the rider's
C2B settles, the B2C payout to the driver fires automatically; we never *park* funds,
run a wallet, or make the driver wait on a batch/cycle. A **faster payout than Bolt is
itself the win** — drivers resent the wait as much as the cut. This is a *bounded-hold*
fallback, explicitly not a *float* business: money is in transit, never at rest.

**Why each tier, in order of preference:**
- *Party-direct (Option B)* — zero float, zero CBK custody posture, Kadogo-free on
  micro-sales. **First choice.**
- *Bounded-hold collect-then-disburse* — re-introduces a *brief* custody window
  (regulatory + B2C cost to confirm), but the hold is seconds and the payout is
  instant. **Fallback only if Daraja forbids party-direct.**
- *True one-call split via a PSP aggregator* — real single-transaction atomicity but
  an aggregator fee that **destroys the zero-fee Kadogo advantage** on ≤ 100 KES
  commerce micro-sales. Revisit only for rides (fares > 100 KES) if the fallback's
  B2C cost ever exceeds it.

> **⚠ Assumption flagged (must verify before payment code).** The choice between
> party-direct and bounded-hold rests on Daraja B2C/till mechanics we deliberately
> chose **not** to verify in this session — **research is the agreed next step.** The
> first task when settlement code begins is to confirm against live Daraja: (1)
> whether a genuine sub-merchant split / party-direct exists at a tariff < 3 %, (2)
> the exact B2C cost + the **shortest** legal hold window and its CBK posture, and (3)
> the auto-disburse latency we can actually guarantee. Do not write settlement on
> memory.

**The locked price is sacred (see §7): it is the *sole* input to the 3 % split.**
Nothing downstream may re-open it.

---

## 7. The bargain state machine — server-authoritative, append-only, price is sacred (locked)

This is the **trust core**. If it is gameable, we are Bolt-with-extra-steps. Bargain
is **optional per ride** (locked): two modes, `instant_fixed` and `bargain`, both
converging on a sacred `PRICE_LOCKED`.

**Principles:**
1. **Server is the sole authority.** The client sends *intents* (offer 250, accept);
   the **server** owns transitions and the final number. The client never computes
   the price.
2. **Append-only, tamper-evident.** Every offer/counter/accept is an immutable row
   (reuse the **hash-chained audit** pattern from P4a). Disputes ("he agreed to
   200!") resolve from the ledger, not he-said-she-said. This is a **product**
   feature — it is how rider trust is earned.
3. **The locked price is the only settlement input.** `accept` is the single
   transition that writes `price_locked`; the 3 % split reads exactly that.
4. **Timeouts everywhere.** A pending offer holds a resource (a waiting driver);
   every pending state has a TTL and auto-expires.

**The machine (both modes converge on `PRICE_LOCKED`):**

```
   instant_fixed:  REQUESTED ──(driver accepts fixed)──────────────► PRICE_LOCKED
                                                                          │
   bargain:        REQUESTED                                             │
                      │ rider offer                                      │
                      ▼                                                  │
                   OFFERED ──(driver accept)──────────────────────────► │
                      │ driver counter                                  │
                      ▼                                                  │
                   COUNTERED ──(rider accept)────────────────────────► │
                      │ rider counter (≤ 3 rounds — locked)             │
                      └──► back to OFFERED                              │
                                                                         │
   any pending ──(TTL expire / either cancels)──► EXPIRED / CANCELLED   │
                                                                         ▼
   PRICE_LOCKED ──(dispatch)──► EN_ROUTE ──► ARRIVED ──► IN_RIDE
   IN_RIDE ──(complete)──► SETTLING ──(3% recorded)──► SETTLED
   SETTLING ──(payment fails)──► SETTLEMENT_FAILED  (retry / dispute)
```

**Guardrails (where Bolt-distrust is won or lost):**
- **Bounded rounds — cap at 3 counters (locked).** Unlimited haggling is a stalling
  DoS on the driver. After 3, it's take-the-last-offer-or-cancel.
- **Sane bounds.** Server rejects absurd offers (negative, 10000× the metered
  estimate). A `bargain` is bounded to a window around a distance/time-metered
  reference, so it is *real negotiation*, not a fraud/fat-finger vector.
- **No silent edits.** Once `PRICE_LOCKED`, neither party can *silently* mutate it.
  The price changes **only** via a consented amendment (see §7.2) — never a unilateral
  edit. Bait-and-switch ("agreed 200, demand 300 at the door") is structurally
  impossible.
- **Idempotency keys** on every state-changing POST — a flaky-network double-tap
  cannot create two offers or double-accept. (Essential on KE mobile.)
- **One open negotiation per (rider, driver) pair** — prevents spamming 50 drivers to
  lock them all.

**Concurrency correctness:** transitions are **compare-and-swap** —
`UPDATE ... WHERE status = :expected_status` (optimistic version). Two simultaneous
`accept`s, or accept-racing-expire, resolve deterministically: exactly one wins, the
loser gets a clean 409. Same discipline as the **first-wins-seen** flag-review row we
already shipped — a proven pattern here, not a new risk.

### 7.1 The metered reference price — `distance × time` (locked)

Bargaining is not a free-for-all; it negotiates **around** a server-computed
reference so offers stay sane and the reroute math (§7.2) has a unit rate to scale.

- **Reference = f(distance, time).** A base rate per km plus a time component
  (captures both trip length *and* expected duration — i.e. traffic). The server owns
  it; the client only displays it. Bargain offers are clamped to a window around it
  (e.g. ±X %); `instant_fixed` simply *is* the reference.
- **Time is traffic-aware (locked).** The duration component is weighted by **live
  traffic** on the planned route (see §7.3) — a jammed 4 km route legitimately meters
  higher than a clear 4 km route. This is the direct answer to the top driver
  complaint that *"Bolt ignores traffic jams."* Traffic raises the **reference**
  (and thus the fair bargaining window) **before** the price is locked — it is *not*
  a mid-trip surcharge.
- The reference is **explainable**: a pure function of stored signals (distance, ETA,
  traffic weight, vehicle class), so a rider can always see *why* the number is what
  it is. Opaque surge is exactly the incumbent grievance we are not repeating.

### 7.2 Mid-trip amendments — reroute & early-termination (locked, with fraud guards)

A locked price assumes the planned route. Reality (jam, riot, accident, blocked road)
can force a longer route or an early stop. Both are handled by the **same primitive: a
consented amendment row** — a mini-locked-price with the full audit sanctity of §7,
chained to the original. The locked price is never silently re-opened.

**Proportional repricing (the founder's formula).** If 4 km metered 100 KES, a forced
10 km detour reprices to `(100 × 10) ÷ 4 = 250` — price scales linearly with the
unit rate from §7.1. Early termination is the same rule downward: stopping at 2 km of
a 4 km / 100 KES trip = `(100 × 2) ÷ 4 = 50`. **A started trip's already-delivered
service must be paid for** — ending early never zeroes the bill.

**The fraud edge this opens, and the four guards that close it.** Raw "longer route =
more money" invites a dishonest driver to detour deliberately and blame traffic. So
reroute repricing is valid **only** when *all* hold:
1. **GPS-measured, never driver-claimed.** Distance comes from the server's breadcrumb
   trail (the §5 pings), not from anything the driver asserts.
2. **Priced on the *optimal* alternate, not the path driven.** If the planned route is
   blocked, we price the **shortest legal detour**, even if the driver wandered
   further. The driver cannot profit from meandering — inflation is capped at the
   honest cost of the diversion.
3. **Cause corroborated.** The detour is price-eligible only if a traffic/incident
   signal (§7.3) confirms a real block on the planned route, **or** the rider
   explicitly consents. No verified cause and no consent → **price stays locked**.
4. **Material change needs consent.** Small variance is auto-absorbed (no nag for a
   100 m wiggle); a material jump **pauses and asks** both parties — accept the new
   amendment, or take the §7.2 *end-now-and-pay-pro-rata* exit. Either way it is an
   explicit, audited, two-party event.

**The in-trip jam decision (founder's ask).** When the system detects a jam on the
route, it can proactively offer both parties a choice: **(a) end the trip here and
settle pro-rata** for distance already covered, or **(b) renegotiate** a new locked
price for the remainder (a fresh bounded bargain). Whichever they pick is a consented
amendment row; the partial service already rendered is always paid.

> **Rule restated:** *the locked price is immutable, but the trip contract can be
> extended or closed early by a new amendment that is consented + cause-verified +
> GPS-measured + optimal-route-capped.* Each amendment is itself sacrosanct and
> hash-chained to the original — the tamper-evident chain is extended, never broken.

### 7.3 Traffic signal — a price *weight*, sourced externally (proposed)

Traffic feeds both the §7.1 reference and the §7.2 reroute corroboration. Sourcing
options, in order of preference:
- A routing/traffic provider (e.g. Google Maps Routes/Distance Matrix with
  `traffic_aware` duration) for ETA-with-traffic, **or** an open alternative (OSRM +
  an open incident feed). Provider choice is an **open question** (§12) — cost,
  licence terms, and Kenya coverage decide it.
- Traffic is a **weight on the reference price and an ETA input**, never an opaque
  multiplier applied after the fact. It must remain explainable (§7.1).
- **Caching:** traffic per road segment is queried at a sane interval and cached in
  Redis, **not** fetched per request — a per-keystroke external call would be a cost
  and latency footgun. Keyed by segment + time-bucket, O(1) lookup.

### 7.4 Driver onboarding / KYC (locked)

Before a driver can be **dispatched** or **receive payment**, they complete identity
verification. Required at signup:
- **National ID** (image).
- **Driving licence** (image).
- **Liveness selfie** — a face-level photo of the driver **holding the ID and licence
  next to their face**. We provide an **example/guide image** so users frame it
  correctly.

Engineering + security notes (these are PII — handle accordingly):
- **KYC documents are sensitive PII under the Kenya DPA 2019.** Store the images in
  the restricted media path (the existing `uploads/` discipline), **never** in the
  feed/public bucket; access is staff-gated, audited, and retained only as long as
  required. Encrypt at rest if the deploy target allows.
- **A driver is `unverified` until a human/automated review approves.** The matcher
  (§5 `GEOSEARCH`) **must filter to `verified` drivers only** — an unverified driver
  is never dispatched and cannot receive a settlement. This gate is enforced
  server-side, not in the client.
- **Verification status rides the RS256 scope** — once approved, the driver's token
  carries `dispatch:eligible`; revocation (fraud, expired licence) flips the §2
  denylist immediately, not at token TTL.
- **Manual review for MVP; automated face/ID match is a later add.** Don't build
  biometric matching now — a staff review queue (we already have the admin/review-row
  pattern from flag-review) is enough to launch and avoids a heavy vendor dependency.

---

## 8. The marketplace must *feel* like social media (locked requirement)

The commerce service is **not** a grid of SKUs — it must read like **Facebook /
TikTok / LinkedIn / X**, because the moat is *personal brand among the people near
you*, and personal brand is a **social** experience, not a catalogue one.

**What "social-media look" means here, mapped to our proximity-native model:**

| Social pattern | Our proximity-native version |
|---|---|
| **Infinite feed** (FB/TikTok/X) | a **proximity feed** — posts/products ranked by *nearness × intent × freshness*, not by follower count. "Discovered instantly by the person next door." |
| **Rich media post** (TikTok/IG) | a sale *is* a post: photo/video, price, "buy" / "bargain" / "get it delivered" inline. The product is the content. |
| **Profile / storefront** (LinkedIn/IG) | every seller (and "every house a shop", §9) has a **profile = storefront**: their listings, ratings, distance-from-you, Confirmed badge if it's a property. |
| **Follow / connect** (X/LinkedIn) | **optional** follow, but discovery does **not** require it — the anti-cold-start core. Proximity replaces the follower graph as the ranking signal. |
| **Engagement** (likes/comments/share) | lightweight social proof (saves, "is this available?", local reviews) that builds *local* trust, not global vanity reach. |
| **Stories / ephemerality** (IG/TikTok) | time-boxed "selling now / fresh stock today" posts that decay — matches perishable/SME inventory. |

**The ranking principle (the algorithm, restated):** normal social media optimizes
**attention** and rewards an existing following — wrong for trade, because it taxes
every small seller with a cold-start problem. Our feed optimizes **proximity +
intent**: *you are surfaced to the people around you where your personal brand is
strongest, no following required.* This is the §9 moat thesis rendered as a UI.

**Engineering implications of the social look:**
- The **feed is a ranked proximity query** (PostGIS radius → score by
  distance/freshness/intent), paginated by cursor. Index-backed, O(log n + k) per
  page — **not** an O(n) scan. Define the ranking as a pure function of stored
  signals so it stays cheap and explainable.
- Media (photo/video) reuses the **existing upload pipeline** (`PE/uploads/`,
  Celery media handling) — do not build a second one.
- **Digital receipts** are auto-generated between buyer and seller on a completed
  sale — append-only, same ledger discipline as §7 (a receipt is the commerce
  analogue of a locked price).
- The **shop admin / POS** (stock counting, reorder thresholds, low-stock alerts)
  lives in the seller's profile/storefront section — POS state feeds the same feed
  (out-of-stock items drop in rank or hide).

### 8.1 The map *is* the proof of proximity — shops on InSAR, blocks that radiate (locked)

The commerce map is not decoration; it **is** the moat made visible. A buyer sees the
seller's shop **on the same InSAR map**, on a real footprint, and reads *"I'm buying
from the shop across the corner / the house two blocks away."* Proximity is felt, not
claimed.

- **Reuse the existing InSAR map + the building-glow primitive.** We already recolour
  a building's **own vertices** (the cyan↔white "breath", O(1)/frame, no overlay
  layer — see the shipped selection-glow). For commerce, **both** the buyer's and the
  seller's building footprints **radiate simultaneously** when they are in contact
  (browsing/chatting/transacting), so each *sees the other light up* — a live "we are
  connected" signal across the two blocks. This is the *same* glow primitive driven by
  a transient pair-state, **not** a new render layer.
- The pair-radiate is driven over the **§5 SSE bus** (a lightweight
  `contact:<pair>` event toggles the glow on/off). It is ephemeral UI state — no new
  storage.
- Footprints are stitched by **`property_uuid`** (§3): the shop's commerce record →
  its InSAR footprint, client-side, no cross-DB join.

### 8.2 Delivery ETA range — "arrives in 10–20 min", traffic-aware (locked)

When a buyer picks a delivery method (foot / bike / motorcycle / taxi), the map
predicts a **range**, e.g. *"Product will arrive between 10 and 20 minutes."*

- **A range, not a false-precision single number.** The low bound = optimal-route ETA
  at the method's speed; the high bound = the same with current **traffic weight**
  (§7.3) and a dispatch/handover margin. A range is honest about uncertainty and sets
  expectations the way a single ticking number never can.
- **Per-method speed profiles.** Foot/bike/motorcycle/taxi each have a speed model;
  motorcycle and foot partially **route around** vehicle jams (a boda filters through
  traffic a taxi cannot), so the traffic weight is applied *per method* — the same
  jam widens a taxi's range more than a boda's. This is a concrete way we *"cater for
  traffic jams"* where incumbents don't.
- ETA reuses the **§7.3 traffic signal and its Redis cache** — one traffic source
  feeds reference price, reroute corroboration, *and* delivery ETA. No duplicate
  integration.
- **Commerce delivery is dispatched through the mobility service (locked, §10).** The
  ETA, the courier match, and the price all come from the same dispatch+geo primitive
  the rides service owns — built once, used twice.

### 8.3 Boost tiers & the reach economy — paid reach without selling rank (locked)

The proximity feed's anti-cold-start purity is the moat, but a feed where promotion
changes *nothing* gives sellers no reason to be active — and **seller activity is the
business.** The resolution is the one every healthy marketplace converges on
(Instagram / Amazon / Etsy / Jumia): **paid reach buys clearly-labelled sponsored
slots interleaved into the organic feed; it never multiplies the organic score.** Two
lanes, not one ranking.

**The two-lane model (the cardinal rule of this section):**

- **Organic lane** stays *exactly* the pure `proximity × freshness × intent` function
  (§8) — deterministic, explainable, cursor-stable, anti-cold-start intact. A close
  un-promoted listing **always wins the organic comparison** against a far one. We do
  **not** add a random "chance to bury" into the core comparison: that would attack
  relevance (the #1 marketplace killer), break the stable keyset cursor (the same row
  would re-appear/vanish between pages), and erode the very moat the feed exists to
  protect.
- **Sponsored lane** — a **bounded, labelled** number of slots per page (config
  `feed_sponsored_every_n`, default 1-in-5) into which *Boost-eligible* listings are
  injected. A far **Sovereign-boosted** listing therefore *does* appear in a distant
  buyer's feed — in a slot marked **"Boosted"** — **without burying** the buyer's
  close organic neighbour, because they occupy different lanes. Both are seen. The
  label *is* the explanation ("why am I seeing this? — Boosted"), the same honesty
  discipline as the Confirmed shield.

**Where the randomness lives (the user's "40% chance", placed correctly):**
Randomness in the *organic neighbour comparison* is harmful; randomness in the
*sponsored-slot lottery* is **healthy** — when more listings are Boost-eligible for a
slot than there are slots, a weighted lottery rotates exposure fairly across promoters
and becomes a tunable **fill-rate dial**. Same idea the user asked for, moved from the
place it hurts to the place it helps.

**The three reach tiers — geographic *eligibility* tiers, not rank-multiplier tiers.**
This maps cleanly onto our model: the organic score already decays to ~0 at the radius
edge, so a Boost's job is purely to make a listing **eligible in the sponsored lane of
a wider audience.** Swahili brand names are locked — the ladder tells its own story.

| Tier | Reach | Free daily allowances | Eligibility mechanism |
|---|---|---|---|
| **Mtaa Boost** | 10 km radius around the seller | **10 / day** | `ST_DWithin(grant_geog, buyer, 10 km)` |
| **Hustle Boost** | 50 km radius **or** a seller-drawn polygon (pick towns / draw on map) | **8 / day** | `ST_DWithin(…, 50 km)` or `ST_Contains(grant_polygon, buyer)` |
| **Sovereign Boost** | nationwide — reaches app users who have never been near the seller | **3 / day** | scope = nation (no geo predicate) |

- **A "boost / allowance / chance" attaches at the seller's choice**, per the user's
  clarification: chosen **at product upload _or_ later on edit**, and a **shop** can be
  boosted too (e.g. a one-time **Sovereign** push to surface the whole storefront
  nationally). `target_type ∈ {listing, shop}`.
- **Allowances = a per-user, per-tier daily quota** (chances), consumed when a grant is
  opened and **reset at local midnight**. Decrement is **CAS-guarded and fail-closed**
  (same ledger discipline as settlement §6) — you can never spend a chance you don't
  have, even under a concurrent double-tap.
- **Paid adverts top up / bypass the free quota and go _global_** (every app user,
  any region). This is the monetisation seam: **build the entitlement mechanism now,
  set the price later** — pricing is simply "how many allowances does X shillings
  buy," and changing it touches config, not schema. A paid grant is the same
  `boost_grant` row with `source = paid` (free vs paid is one column).

**Engineering guardrails (our O(bounded) + security rules applied):**
- **Nationwide reach must not become an O(everyone) query.** A Sovereign grant is
  eligible in *every* feed request in the country, so we **never** scan all active
  grants per request. The sponsored lane is a **separate, bounded candidate pull**:
  top-K active grants whose scope **contains the buyer** (GiST-indexed point-in-radius
  / point-in-polygon / nation), capped at `feed_sponsored_max_candidates`, then the
  lottery fills the reserved slots. One extra indexed query, **O(log n + K)** — the
  same bounded-window discipline as the organic candidate pull.
- **Relevance guardrail + the metric to watch.** Sponsored share of the feed is capped
  (the 1-in-N dial) and every sponsored row is labelled. The health metric is **buyer
  conversion**: if national inventory floods local feeds and conversion drops, tighten
  the fill rate. We **build the dial and tune it with data**, never set-and-forget.
- **Boost is orthogonal to §8 ephemerality, not a replacement.** The shipped
  evergreen/story "selling now" window is the *temporal* dimension (a freshness nudge
  **inside the organic lane** — unchanged). A Boost tier is the *spatial + entitlement*
  dimension (sponsored-lane eligibility + reach). They compose; nothing already built
  is discarded.
- **Authorisation reuses the existing discipline:** a grant is owner-only off the
  verified token `sub`; a cross-owner target is **404** (no existence leak); opening a
  grant is **idempotent** under an idempotency key, exactly like order open/settle.

**Why this is the right call (product rationale, for the record):** it is the
difference between **renting visibility** (healthy, repeatable revenue that grows with
seller activity) and **selling rank** (a one-time sugar high that erodes the relevance
asset you are renting). Sellers get real, visible reach; active promoters are rewarded
with exposure; brand-new sellers still get discovered organically; and we get a clean
paid-advert upsell — all without compromising the feed a buyer opens the app to see.

### 8.4 The social feed UI — public comments, post kinds, the Listings|Videos toggle (shipped, backend)

The §8 "feel like social media" requirement, made concrete on the buyer feed. **Backend
shipped this increment; the frontend redesign is the next slice.**

- **Public comment thread per post.** A new `listing_comments` table — **append-only**
  (same ledger discipline as inquiries/receipts), `author_uuid` = the weespas token `sub`
  (never a cross-DB FK). Distinct from a `ListingInquiry`: an inquiry is the **private**
  buyer→seller "is this available?" landing in the seller inbox; a **comment is public**,
  shown inline under the post to everyone. Endpoints: `POST /listings/{id}/comments`
  (buyer scope, body trimmed + length-capped, 404 on missing/inactive listing, 422 on
  empty/oversized) and `GET /listings/{id}/comments` (keyset-paginated newest-first). A
  `hidden` boolean is the **moderation seam** — ships inert (no endpoint), so soft-hiding
  an abusive comment later is non-destructive (the append-only row stays; reads filter it).
- **`comment_count` on the feed item** — a single batch `GROUP BY` over the whole page (no
  N+1), **display-only**: it never enters the ranking score, exactly like `save_count` and
  `seller_rating`. Engagement must never let a noisy post bury a closer quieter one — that
  would reintroduce the cold-start the proximity feed exists to kill.
- **Post kind + the `Listings | Videos` toggle.** A video can appear in the *normal* feed;
  the toggle does **not** sort by media type. It distinguishes the seller's **declared post
  kind**: an `is_short_video` boolean on the listing marks a dedicated reel-style post. The
  feed takes `?kind=listings|videos` (omit ⇒ both), index-backed on `is_short_video`, and it
  filters **both lanes** (organic + sponsored) so a Videos view never leaks an ordinary
  boosted listing. An unknown `kind` is a **422** (never a silent unfiltered fallback).
- **The 250 MB short-video size cap is enforced on the seller upload path (FE-2), not the
  feed** — a post-kind flag here, a media constraint there; kept separate on purpose.
- **Shops stay a catalogue, the feed is social.** The public storefront DTO is unchanged
  and deliberately *not* social-media-styled (no engagement bar) — only the feed carries the
  social affordances. This is a locked design split (Millennial/GenZ feed, traditional shop).
- **Comment moderation (shipped).** The `hidden` seam is now **active**: `PATCH
  /comments/{id}/hidden` soft-hides/un-hides a comment. Authorized for a **staff** principal
  OR the **seller who owns the comment's listing** (own-your-thread). A non-owner/non-staff
  caller — or a missing comment — gets **404, never 403** (no existence leak, S6). The row
  stays (append-only audit); every read path already filters `hidden`, so a hide takes effect
  on the thread + counts + like-by-id at once.

### 8.5 Trending rail — boosted PRODUCTS, per-slot decay (shipped; redesigned)

A second, always-visible surface for **boosted PRODUCTS** (listings), alongside the in-feed
sponsored lane. The boosted listing appears in **BOTH** (product decision) — the lane is unchanged;
the rail is additive. (Original v1 was a left-side scrolling ticker of boosted *shops*; this is the
v2 redesign.)

- **Boosted PRODUCTS, not shops.** Each card is a product: **title + price + category color +
  category icon** — the lunchtime case study is a hotel boosting "Nyama Choma / KES 350 / 🥩". There
  is deliberately **no shop name/avatar** on the card; the color + icon are the at-a-glance trade
  signal. Tapping still opens the seller's storefront. **Shop-level boosts are EXCLUDED** from
  trending (they surface only in the in-feed sponsored lane) — so trending is `target_type='listing'`
  only. A sold-out / inactive listing is filtered out (it must never advertise a buyable price).
- **Shop categories.** A `category` slug (allow-list of ~11 trades —
  butchery/bakery/greengrocer/restaurant/boutique/electronics/shoes/beauty/hardware/pharmacy/
  general; single source of truth in `commerce/core/categories.py`), carried onto the product card
  off its owning shop. Validated at the API edge (422 on unknown; `None` = un-categorised). The
  **slug→color map is frontend-only** (`utils/categories.ts` + `--color-cat-*` tokens) and the
  per-slug **icon glyph** lives in `components/trade/CategoryIcon.tsx` — the wire carries only the
  slug, so a palette/icon re-tune never touches the API. pytest + vitest parity tests assert the
  slug lists (backend↔frontend) and the glyph set (every slug) can't drift.
- **The rail = a fixed-position card on the RIGHT** (not a long strip): rounded, flush from the
  navbar's bottom edge to the viewport bottom (mirroring the AgentsPage VerticalVideo column
  height), ~248px wide; the feed/timeline column moves LEFT and the page reserves the right gutter
  at ≥1101px. Hidden <1100px so the feed owns narrow screens. Cards are **category-colored** with
  rail-scoped **+25% contrast** vs the feed tint (a darker derived accent for AA-legible small
  glyphs), always labelled "Boosted".
- **Deterministic queue + CLIENT-OWNED per-slot decay.** `GET /trending?lat&lng` returns the full
  bounded QUEUE of boosted products reaching the buyer's **locality bucket** (lat/lng snapped to a
  ~1.5 km grid), reusing the §8.3 Boost eligibility/scope machinery (two grants on one listing
  collapse to the widest tier). The server does **no rotation**: it returns `visible_slots` (how
  many cards show at once) + `slot_seconds` (each card's lifetime). The CLIENT renders that many
  slots and decays **each slot on its own staggered timer** — when a slot's card expires the next
  queued product takes its place, so **any** card can flip independently (not a synchronized FIFO
  scroll, which would free only one slot per tick and starve the queue). A shared skip-visible
  pointer gives every queued product fair airtime; the swap is O(visible_slots) amortized O(1).
  `slot_seconds` is traffic-determined: full `base` (12 s) when the queue fits the cap, shrinking
  toward a `min` floor (6 s) under contention — **always > 5 s** so a card is readable. Decay
  **pauses** on hover/focus (a card can't vanish mid-read/tap) and **freezes** under
  `prefers-reduced-motion`. No PII — opaque ids + seller-published fields only (S6).
- **Realtime = polling, not SSE (lead-engineer decision).** The queue membership is a deterministic
  function of the bucket, so it's **Redis-cached per bucket** (TTL = `poll_seconds`) and the client
  re-polls at `poll_seconds` to refresh the queue; the decay animation is **client-local**, so the
  cache need not track sub-slot windows. Every nearby buyer shares one compute per poll window — far
  cheaper than a per-viewer SSE connection + Pub/Sub fan-out. The cache **fails OPEN** (a Redis blip
  recomputes from the DB; the rail is a discovery surface, not a security boundary). Note this drops
  the v1 cross-viewer lock-step rotation: co-located viewers share the same queue but their decay
  phases are seeded locally at load, so the visible cards may differ moment-to-moment — intended.

---

## 9. "Every house a shop" — the through-line (from commercial_model §9)

The life-event bundle is **find house → book a mover → transport (taxi/boda) →
furnish (shops)**. The trading layer is how Weespas owns that whole moment:
listings + InSAR safety badge (already built) → mobility (movers/boda/taxi) →
commerce (shops/neighbour selling). Same identity, same geo graph, same M-Pesa rail,
same `property_uuid`. "Every house a shop" is the end-state; this doc builds the two
services that get there.

---

## 10. Sequencing — commerce first (locked)

`commercial_model.md` §9 locks the *product* order: prove house-search + InSAR-CAC →
movers/transport → shops. Within the trading layer the **engineering** order is now
decided: **build commerce first.**

1. **Shared rails first** — RS256 audience for trade, the PostGIS+Redis geo
   foundation, the SSE+Pub/Sub event bus. Both services need these.
2. **commerce (the e-commerce/social marketplace)** — reuses existing rails most
   directly (uploads, receipts, proximity query, the InSAR map + glow primitive) and
   has the gentler failure profile, so it proves the social-feed moat with the least
   new realtime risk. Includes the shop-on-InSAR map, radiating-blocks contact signal
   (§8.1), social feed (§8), POS/storefront, and digital receipts.
3. **mobility** — dispatch + bargain + settlement + traffic-aware pricing, the
   realtime-heavy service, once the rails and the feed are proven.

**Note on the dependency:** commerce **delivery ETA + courier dispatch** (§8.2) is
served *through* the mobility service (locked, §12). So commerce-first ships the
**social marketplace + buy flow** fully, with delivery initially a *thin* dispatch
slice that the full mobility build (step 3) later deepens (bargaining, reroute,
multi-method pricing). Commerce doesn't block on all of mobility — only on the minimal
courier-match primitive.

---

## 11. Locked decisions (index)

1. **Separate DB / separate layer** — trade traffic never bottlenecks InSAR pipelines, and vice-versa.
2. **Two services sharing rails** — `mobility` + `commerce`; shared identity/geo/payment, separate transaction models (and separate settlement mechanisms).
3. **Asymmetric stateless auth (RS256 JWT)** — public key copied to each service, verify in-memory, zero internal network calls. Reuses the existing telemetry-bridge pattern.
4. **Revocation gate on money actions** — stateless fast path for reads; O(1) Redis denylist check before settlement/dispatch.
5. **Synchronized `property_uuid`** across DBs — client-side concurrent stitching, no cross-DB join. Reuses the Confirmed-shield batch pattern.
6. **PostGIS** (durable geo graph) **+ Redis GEO** (live driver positions) — split by access pattern; avoids GiST write-amplification.
7. **Mobility uses `asyncpg` + GeoAlchemy2** (not sync psycopg2) — greenfield DB, realtime path.
8. **SSE downlink + HTTP uplink + Redis Pub/Sub** — auto-reconnect for flaky networks, RS256 auth fits, horizontally scalable, no sticky sessions. WebSocket deferred.
9. **Live driver-dot tracking = Phase 2.**
10. **Settlement = party-direct + separate 3 %, float-free (preferred); bounded-hold collect-then-disburse (fallback)** — "atomic" is a ledger guarantee, not a rail guarantee. If Daraja forbids party-direct, fall back to *shortest-possible hold* (seconds, auto-disburse, never park/wallet). Verify Daraja mechanics before any payment code.
11. **Bargain = server-authoritative, append-only, optional per ride, capped at 3 rounds**, CAS-guarded transitions; the locked price is the sole settlement input.
12. **The commerce marketplace must look/feel like social media** (FB/TikTok/LinkedIn/X) — proximity-and-intent feed replaces the follower graph; a sale is a post; a profile is a storefront.
13. **Sequence = commerce first**, then mobility; shared rails before either.
14. **Metered reference price = f(distance, time), traffic-aware** — bargain offers clamp to a window around it; the unit rate drives proportional reroute/early-termination repricing.
15. **Mid-trip amendments (reroute / early-termination)** — consented + cause-verified + GPS-measured + optimal-route-capped; proportional `(rate × actual_km)`; already-rendered service is always paid; never a silent edit.
16. **Driver KYC = ID + licence + liveness selfie (holding both at face level)**; matcher dispatches `verified` drivers only; documents are restricted PII.
17. **The map is the proof of proximity** — shops shown on the InSAR map; buyer & seller footprints **radiate simultaneously** (reuse the glow primitive) when in contact; stitched by `property_uuid`.
18. **Delivery ETA = a traffic-aware range** ("10–20 min"), per delivery method; **commerce delivery dispatches through the mobility service** (built once, used twice).
19. **Boost = paid reach, never paid rank (two-lane feed)** — the organic lane stays the pure proximity×freshness×intent function (anti-cold-start moat, deterministic, cursor-stable); promotion buys **labelled sponsored slots** interleaved at a capped 1-in-N rate, filled by a weighted lottery. No "chance to bury" in the organic comparison.
20. **Three reach tiers, eligibility not multipliers** — **Mtaa Boost** (10 km, 10/day) / **Hustle Boost** (50 km or drawn polygon, 8/day) / **Sovereign Boost** (nationwide, 3/day); allowances are a per-user per-tier **daily quota** (chances), CAS-decremented + fail-closed + midnight reset; a grant attaches to a **listing or a shop**, chosen at upload or edit.
21. **Paid adverts = the monetisation seam** — a paid grant goes global (all app users) and tops up/bypasses the free quota; `source ∈ {free, paid}` is one column. **Build the entitlement mechanism now, price later** (pricing is config, not schema). Sponsored candidate pull is a separate **bounded** GiST query (top-K scope-containing grants), never an O(everyone) scan.

---

## 12. Open questions (still need input)

- **Working name** for the layer — placeholder "weespas-trade"; candidates floated:
  *coins / streets / hawker* (or something more creative). **Deferred to later.**
- **Traffic-data provider** (§7.3) — Google Maps Routes/Distance Matrix vs an open
  stack (OSRM + open incident feed). Decided by cost, licence terms, Kenya coverage.
- **Settlement mechanics** (§6 ⚠) — the agreed **research step**: confirm against live
  Daraja whether party-direct works, else the shortest legal auto-disburse hold + its
  CBK posture. Gates all settlement code.
- **Pricing constants** — base rate per km, time weight, the bargain window ±%, and the
  per-method speed profiles (foot/bike/motorcycle/taxi). Need real Nairobi numbers.
- **Boost pricing & free-quota tuning** (§8.3) — what a paid advert costs, how many
  allowances X shillings buys, and whether the free daily quotas (10 / 8 / 3) are the
  right starting numbers. **Deferred by design:** the mechanism is built quota-and-
  config-driven so pricing lands without a schema change. Also TBD: the sponsored
  fill-rate (`feed_sponsored_every_n`) and the lottery weighting, both to be tuned
  against buyer-conversion once there is live traffic.
- **KYC review** — manual staff review for MVP (agreed); when/whether to add automated
  face↔ID biometric matching is a later call.
