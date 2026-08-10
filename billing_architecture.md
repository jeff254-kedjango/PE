# billing_architecture.md — Weespas billing & entitlements (buildable spec)

> **Status:** technical design of record, 2026-06-22. This is **doc B** — the *buildable spec*
> that turns the strategy in `commercial_model.md` into something we can implement.
>
> **✅ Reconciliation note (2026-06-24): this spec is now BUILT.** The original "not yet code"
> line is stale. All seven build-order steps in §11 have shipped (see the checklist there) and
> the §11 unit/integration suites are green. The per-file map: `entitlement_service.py`,
> `geo_fuzz.py`, `reveal.py`, `billing_service.py` + `mpesa_client.py` + `billing_tasks.py`,
> `billing_tiers.py`, the `billing`/`reveal`/`metering`/`policy` routers, `policy_engine.py` +
> `policy_tasks.py`, two Alembic migrations (`a1b2c3d4e5f6_billing_tables`,
> `b2c3d4e5f6a7_metering_tables`); frontend `TierChooserModal`, `ProScaleModal`, `SoftGate`,
> `RevealContext`, `api/billing.ts`, `api/policy.ts`. M-Pesa runs on the Daraja **sandbox**
> (`MPESA_ENV=sandbox`) — production shortcode is the only remaining flip. The §-by-§ design
> below matches the implementation; treat any divergence as a doc bug to fix against the code.
>
> **Read `commercial_model.md` first** for the *why*. This doc is the *how*: the data model, the
> M-Pesa STK flow, the reveal endpoint + coordinate fuzzing, the O(1) Redis entitlement, the
> reconciliation discipline, and the metering/policy engine.
>
> **Grounding (verified in code, not assumed):**
> - Listing coordinates live on the **`Address`** model (`models/property.py:81–100`):
>   `latitude`/`longitude` as `Numeric(9,6)`, FK→`properties` CASCADE, indexed.
> - They are serialized to clients via **`AddressResponse.latitude/longitude`** (floats) in
>   `schemas/property.py` — **this is the single fuzzing chokepoint.**
> - Redis is a **module-level client**: `services/cache.py` → `redis_client =
>   Redis.from_url(settings.redis_url, decode_responses=True)`. We reuse it.
> - Idempotency precedent already exists: `services/celery_helpers.py:61` uses
>   `redis_client.set(key, "1", nx=True, ex=ttl)`. Reconciliation uses the same primitive.
> - Verified phone numbers already exist from OTP signup (`User.phone`) → STK targeting is free.
> - Property read endpoints that return an address (all need the fuzzing pass):
>   `routers/properties.py` → `list_properties`, `search_properties`, `get_nearby_properties`,
>   `get_featured_properties`, `filter_properties`, `list_shorts`, `get_property`,
>   `get_related_properties`.

---

## 0. Design goals & invariants

1. **O(1) hot paths.** The entitlement check on every reveal is constant-time Redis ops. (Founder
   constraint: complexity must stay O(1) or better on the request path.)
2. **Zero float held.** Direct STK pay-per-window; we never store a customer balance (avoids CBK
   PSP / e-money licensing — see `commercial_model.md §11`).
3. **Exact coordinates are a server-gated secret.** They never reach a client until a reveal is
   paid for. Fuzzing happens **server-side at serialization**, never "hidden" client-side.
4. **Idempotent money.** Every M-Pesa callback is processed at-most-once by transaction id; a
   debit can never silently fail to grant, and a grant can never be double-applied.
5. **Fail-safe.** If Redis is unavailable the reveal is *denied* (never accidentally granted); if
   the entitlement system is down, browsing/discovery is unaffected (it's free anyway).
6. **Additive & non-destructive.** New tables via Alembic allow-list (same discipline as P4a); no
   changes to existing auth, listings, or the InSAR path.
7. **The serving/read paths stay honest.** Discovery (details/photos) is never gated; only the
   precise navigable location is.

---

## 1. Component map

```
                 weespas-frontend
                       │
   (1) tap map pin / "Get directions"  ─────────────▶  POST /api/v1/reveal/{listing_id}
                       │                                      │ entitlement check (O(1) Redis)
   (2) no window → chooser modal                              ├─ has slot ─▶ exact coords
                       │                                      └─ none      ─▶ 402 + chooser hint
                       ▼
   POST /api/v1/billing/checkout {tier}  ──────────▶  Billing service
                       │                                      │ STK Push (Daraja/AT)
                       │                                      ▼
                       │                               Safaricom M-PESA
                       │                                      │ async callback
   (3) poll GET /billing/checkout/{id} ◀──────────  POST /api/v1/billing/mpesa/callback
                       │  (status: pending→paid)             │ idempotent on txn id
                       ▼                                      ▼
              window granted (Redis)  ◀───────────  grant_window(user, tier)
                                                              │
                                                     payment_ledger (Postgres, append-only)
```

Three new pieces, all in Weespas backend:
- **`services/entitlement_service.py`** — the Redis reveal/window primitive (O(1)).
- **`services/billing_service.py`** — STK Push initiate + callback reconcile + ledger writes.
- **`services/geo_fuzz.py`** — the coordinate-blurring used by the property serializer.

Plus a new router `routers/billing.py` and a new router `routers/reveal.py`, the Alembic tables in
§3, and a small change to the property response serialization (§5).

---

## 2. The entitlement primitive (Redis, O(1))

A purchased **window** buys *N reveals* for *T seconds*. State is two Redis keys per user, both
expiring with the window so there is **no cleanup job**:

```
ent:{user_id}:window   → HASH { tier, quota, granted_at, txn_id }   TTL = T
ent:{user_id}:unlocked → SET  of listing_id                         TTL = T
```

**Why a SET, not a counter:** the SET makes re-revealing an already-unlocked listing **free and
idempotent** (you can re-open directions to a place you paid for) *and* makes the quota the SET's
cardinality — one structure does both. A bare decrementing counter couldn't tell "new listing"
from "re-open."

### 2.1 `reveal(user_id, listing_id) -> RevealResult`

```
key_w = f"ent:{user_id}:window"
key_u = f"ent:{user_id}:unlocked"

1. if not redis.exists(key_w):              return NO_WINDOW          # → chooser
2. if redis.sismember(key_u, listing_id):   return REVEALED (exact)   # free, idempotent
3. if redis.scard(key_u) >= quota:          return QUOTA_EXHAUSTED    # → upgrade
4. redis.sadd(key_u, listing_id)            return REVEALED (exact)   # consumes one slot
```

All four steps are O(1) (`EXISTS`/`SISMEMBER`/`SCARD`/`SADD`). `quota` is read from the window
hash (one `HGET`). To make steps 3–4 atomic under concurrent taps (two pins tapped at once near
the quota edge), implement as a tiny **Lua script** (`EVALSHA`) doing sismember → scard → sadd in
one round-trip; this prevents a race that lets `quota+1` reveals through. Still O(1).

### 2.2 `grant_window(user_id, tier, txn_id)`

```
T, N = TIERS[tier].window_seconds, TIERS[tier].quota
pipe = redis.pipeline()
pipe.delete(key_u)                                   # fresh window starts empty
pipe.hset(key_w, mapping={tier, quota:N, granted_at, txn_id})
pipe.expire(key_w, T); pipe.expire(key_u, T)
pipe.execute()
```

A **new** purchase replaces any existing window (we do not stack windows — simpler, and the user
just bought the bigger thing). If we later want top-ups, `grant_window` becomes additive on quota
and `max()` on TTL; the call site doesn't change.

### 2.3 Tier table (single source of truth, mirrors `commercial_model.md §5`)

```python
TIERS = {
  "T1": Tier(price_kes=20,  quota=3,  window_seconds=2*3600),
  "T2": Tier(price_kes=50,  quota=6,  window_seconds=4*3600),
  "T3": Tier(price_kes=100, quota=10, window_seconds=24*3600),
}
HOOK = Tier(price_kes=0, quota=1, window_seconds=30*60)   # free, see §7
```

Prices/quotas are config-overridable (we will tune them — `commercial_model.md §5.1` says the
ladder is not set in stone). Keep the table in `core/config.py` or a small `billing_tiers.py`.

### 2.4 Fail-safe

If Redis raises on a reveal, return `NO_WINDOW`/deny (never grant on error). Browsing is
unaffected because the property feed's fuzzing (§5) is independent of the entitlement read.

---

## 3. Persistence (Alembic, additive, non-destructive)

Two new tables, created via the **same include-object allow-list discipline as P4a** (so
autogenerate emits only these, never touches the 17 legacy tables).

### 3.1 `payment_intent` — one row per checkout attempt

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | our checkout id (returned to FE for polling) |
| `user_id` | FK→users | who is paying |
| `phone` | varchar | MSISDN the STK went to (from `User.phone`) |
| `tier` | varchar | T1/T2/T3 |
| `amount_kes` | int | tier price at time of purchase (audit) |
| `merchant_request_id` | varchar, idx | from STK initiate response |
| `checkout_request_id` | varchar, unique idx | from STK initiate response — the join key to the callback |
| `status` | varchar | `pending` / `paid` / `failed` / `expired` |
| `mpesa_receipt` | varchar, unique nullable | the M-Pesa confirmation code (e.g. `SGR1A2B3C`) |
| `result_code` | int nullable | Daraja `ResultCode` (0 = success) |
| `created_at` / `updated_at` | timestamptz | |

### 3.2 `payment_ledger` — append-only record of *settled* money

| Column | Type | Notes |
|--------|------|-------|
| `id` | bigint autoincrement PK | |
| `intent_id` | FK→payment_intent | |
| `user_id` | FK→users | |
| `mpesa_receipt` | varchar **unique** | **the idempotency key** — a callback for an already-ledgered receipt is a no-op |
| `amount_kes` | int | |
| `tier` | varchar | |
| `window_seconds` / `quota` | int | what was granted (audit of the grant) |
| `created_at` | timestamptz | |

`payment_ledger` is **append-only** — same posture as `notification_audit` (P4a). No UPDATE/DELETE
(optionally enforce with the same BEFORE-UPDATE/DELETE trigger pattern). It is the source of truth
for "what did this user pay and what did we grant," and the reconciliation idempotency anchor.

> **Why two tables:** `payment_intent` is mutable checkout *state* (pending→paid); `payment_ledger`
> is the immutable record of *settled* transactions. Separating them keeps the money record clean
> and tamper-evident while letting checkout state churn.

---

## 4. The M-Pesa STK Push flow (Daraja C2B / STK)

### 4.1 Initiate — `POST /api/v1/billing/checkout {tier}`

1. Auth required (JWT). Resolve `User.phone` (already verified at signup).
2. Create `payment_intent(status=pending, amount=TIERS[tier].price)`.
3. Call Daraja **STK Push** (`/mpesa/stkpush/v1/processrequest`) with `BusinessShortCode`, the
   `Amount`, `PartyA=phone`, `PartyB=shortcode`, `CallBackURL`, `AccountReference=intent.id`,
   `TransactionDesc="Weespas <tier>"`.
4. Store the returned `MerchantRequestID` + `CheckoutRequestID` on the intent.
5. Return `{checkout_id, status:"pending"}` to the FE. The phone now shows the PIN prompt.

**Cost note (`commercial_model.md §10`):** all three tiers are ≤100 KES → **zero M-Pesa fee to the
customer** (Kadogo) and, on Buy Goods ≤200, **zero to us**. Micropayments are viable here.

### 4.2 Callback — `POST /api/v1/billing/mpesa/callback` (Daraja → us)

This endpoint is **public** (Safaricom calls it) but **safe** because it trusts nothing in the body
except as a *lookup key*, then verifies:

1. Parse `CheckoutRequestID` + `ResultCode` (+ `CallbackMetadata` for `MpesaReceiptNumber`,
   `Amount`, `PhoneNumber`).
2. **Look up** the `payment_intent` by `checkout_request_id`. Unknown → 200 OK + log (never error
   back to Safaricom; they retry on non-200).
3. **Idempotency gate:** `redis.set(f"mpesa:cb:{receipt}", 1, nx=True, ex=86400)` — the same
   `SET NX EX` primitive already used in `celery_helpers.py:61`. If the key already exists, this
   callback is a duplicate → 200 OK, do nothing. Belt-and-braces: `payment_ledger.mpesa_receipt`
   is `UNIQUE`, so a duplicate that races past Redis fails the insert harmlessly.
4. **Verify** `ResultCode == 0` and `Amount == intent.amount_kes` (reject mismatched amounts).
5. In **one DB transaction**: insert `payment_ledger` row + set `intent.status=paid`,
   `intent.mpesa_receipt=...`.
6. After commit: `grant_window(user_id, tier, receipt)` (§2.2). The user's Redis window is now live.
7. Return `{"ResultCode":0,"ResultDesc":"Accepted"}`.

If `ResultCode != 0` (user cancelled / timeout / insufficient funds): set `intent.status=failed`,
no ledger row, no grant.

### 4.3 The debit-but-callback-fails case (the one real risk)

M-Pesa took the money but the callback never reached us (network blip). Two backstops:

- **FE poll:** `GET /api/v1/billing/checkout/{id}` returns `intent.status`. The FE shows
  *"confirming payment…"* and polls every ~3 s for up to ~60 s. When the callback lands, status
  flips to `paid` and the reveal proceeds.
- **Reconciliation sweep (Celery beat, every ~2 min):** for each `pending` intent older than ~30 s,
  call Daraja **STK Query** (`/mpesa/stkpushquery/v1/query`) with the `CheckoutRequestID`. If it
  reports success and we have no ledger row, run the *same* idempotent settle path (§4.2 steps 3–6).
  This guarantees a paid user eventually gets their window even if the callback was lost — and the
  Redis+UNIQUE idempotency guarantees they get it **exactly once**.

> Reconciliation reuses the existing Celery infrastructure (`core/celery_app.py`, beat is already a
> wired concept in `celery_helpers.py`). New queue: `billing`.

---

## 5. Coordinate fuzzing — the server-side reveal gate

**The single chokepoint:** wherever an `Address` becomes an `AddressResponse`. Implement once, in a
serializer/dependency, so **every** property endpoint (§ grounding list) is covered uniformly — a
new endpoint can't accidentally leak exact coords.

### 5.1 `services/geo_fuzz.py`

```python
def fuzz(lat, lon, *, listing_id) -> tuple[float, float]:
    """Deterministic blur to a ~FUZZ_RADIUS_M neighbourhood blob.
    Deterministic per listing so the blurred pin doesn't 'jump' between
    requests (which would look broken and hint at the real point)."""
    # snap to a grid whose cell ≈ 2*FUZZ_RADIUS_M, offset by a per-listing
    # hash so the blob isn't trivially un-snappable to grid centres.
```

- **Deterministic** (seed = `listing_id`): the fuzzy marker is *stable* across requests. A random
  jitter per request would (a) look glitchy and (b) let an attacker average many requests back to
  the true point. Determinism prevents both.
- `FUZZ_RADIUS_M` is config (`insar`-style), **default ~1000 m**, and is the **tunable conversion
  lever** from `commercial_model.md §3.4` — A/B it.

### 5.2 Serialization rule

When building `AddressResponse` for a listing `L` requested by user `U`:

```
if entitlement.is_revealed(U, L.id):   # SISMEMBER, O(1) — only true after a paid reveal
    lat, lon = exact
else:
    lat, lon = fuzz(exact, listing_id=L.id)
# also coarsen street_address (drop house number) when not revealed
```

- For **anonymous** users (no `user_id`) → always fuzzed.
- The list/feed endpoints fuzz **every** row (a list view never reveals — reveals are one listing
  at a time via §6). This is one `SMEMBERS`/pipeline of `SISMEMBER` per page; for a page of ~20
  it's negligible, and usually the unlocked set is empty so we skip the check entirely.
- **Also blur `street_address`** when not revealed — the exact street + house number is as
  revealing as the pin. Keep `city`/`county`/`location_name` (they're the discovery signal).

### 5.3 What stays free (never fuzzed away)
`location_name`, `city`, `county`, the photos, price, description, and the *approximate* marker.
The user sees "a 1-bed in South C, ~here" — enough to want it, not enough to walk to it.

---

## 6. The reveal endpoint — `POST /api/v1/reveal/{listing_id}`

The only endpoint that returns **exact** coordinates.

```
POST /api/v1/reveal/{listing_id}     (JWT required)
→ entitlement.reveal(user_id, listing_id):
    NO_WINDOW        → 402 Payment Required  {reason:"no_window", tiers:[...]}   # FE opens chooser
    QUOTA_EXHAUSTED  → 402 Payment Required  {reason:"quota", tiers:[...]}       # FE offers upgrade
    REVEALED         → 200 {lat, lon, street_address, directions_url}            # exact
```

- On `REVEALED`, also emit a **metering event** (§8): `reveal {user, listing, aoi?, ts}`.
- `directions_url` (MVP) = a `geo:` / Google-Maps deep link with the exact destination, so the FE's
  "Get directions" just opens the device map app (zero mapping cost — `commercial_model.md §4.3`).
- 402 is the correct status (Payment Required); the FE maps it to the chooser modal. This is the
  *first-reveal* prompt point — option (ii): the map was free and fuzzy up to here.

**Idempotent & safe:** re-calling reveal for an already-unlocked listing returns exact coords again
without consuming a slot (step 2 of §2.1).

---

## 7. The free hook tier

`commercial_model.md §5.1` gives every user **1 free reveal / ~30 min / small radius** as CAC for
the core product. Implementation: a *virtual* window granted without payment, once per cooldown:

```
if not redis.exists(key_w) and redis.set(f"hook:{user_id}", 1, nx=True, ex=HOOK_COOLDOWN):
    grant_window(user_id, "HOOK", txn_id="free")
```

The `hook:{user}` key with a long cooldown (e.g. 24 h) prevents farming the free reveal in a loop.
The first paid window supersedes it. This gets the *first* exact location in front of a new user so
they feel the anti-scam value before paying.

---

## 8. Metering & the policy engine (ties into company-detection, `commercial_model.md §7`)

Reuse the **existing telemetry spine** (`user_sessions` + behavioural events, per `work_flow.md
§9.5`) — do **not** stand up a second analytics store.

### 8.1 New events (emitted to the same session-anchored pipeline)
`reveal`, `map_open`, `directions_open`, `checkout_initiated`, `checkout_paid`, plus the InSAR-side
`insar_building_view` / `insar_export` (for company detection). Each carries `{user_id?, session_id,
listing_id|building_id, ts}`.

### 8.2 The policy engine — `entitlement.gate(user, action) -> free | metered | blocked`
A thin function combining two checks already designed:
1. **`require_role`** (exists, `services/auth_service.py`) — staff/admin/professional/authority →
   `free` for InSAR commercial actions (the vetted-role exemption, `commercial_model.md §7.3`).
2. **Commercial-likelihood score** (`commercial_model.md §7.2`) — computed offline by a Celery beat
   job over the telemetry, written to a small `user_usage_profile` (volume, breadth, automation,
   corporate-domain, export-rate). Above threshold + no free role → `metered` (soft gate: "you're
   at professional scale, here's a business plan"), never `blocked`/accusatory.

This is **separate** from the per-reveal entitlement (§2): §2 monetizes *individual house-hunting*;
§8 governs *InSAR commercial use by companies*. Two different revenue lines, one telemetry spine.

---

## 9. Frontend changes (weespas-frontend)

1. **`api/config.ts`** — the existing `fetchJson` already sends `credentials:'include'`; add a
   helper that maps **402** to "open chooser modal" instead of the **401**→login redirect.
2. **Chooser modal** (new `components/billing/SubscriptionModal.tsx`) — the 20/50/100 cards; on
   pick → `POST /billing/checkout` → show *"confirming payment…"* → poll
   `GET /billing/checkout/{id}` → on `paid`, retry the pending reveal.
3. **Map (`PropertyMap.tsx`)** — render the **fuzzy** markers from the (already-fuzzed) feed; on pin
   **tap** call `POST /reveal/{id}`; on 402 open the modal; on 200 swap the marker to the exact pin.
   **No pop-up on entering Map view** (option ii).
4. **"Get directions" button** (new, on `PropertyCard` + `PropertyDetails`) — calls `reveal`; on 200
   opens `directions_url`; on 402 opens the modal.
5. **Entitlement status chip** (optional) — show "N reveals left · expires 14:32" from a small
   `GET /api/v1/entitlement/me` (reads the window hash + `scard`). Pure UX.

---

## 10. Security & abuse (specific to billing)

- **Exact coords never in a list payload** — only via `POST /reveal` (§5/§6). This is the core
  anti-scrape control; without it the model leaks for free.
- **Callback endpoint trusts nothing** — body is a *lookup key*; we verify `ResultCode`, `Amount`,
  and dedupe on receipt (Redis NX + UNIQUE). Optionally allow-list Safaricom callback IPs.
- **Reveal is JWT-gated** so reveals are attributable (and meterable for §8). Anonymous users get
  the fuzzy map + the chooser, but must sign in (free, OTP) to actually pay/reveal — which also
  links the `user_sessions` row (P4a one-time link) for telemetry.
- **No replay of a window** — `grant_window` keys off the *receipt*; a receipt is ledgered once
  (UNIQUE) so a replayed callback can't mint a second window.
- **Rate-limit `checkout`** (reuse the OTP rate-limit pattern, `auth_service.py`) so a user can't
  machine-gun STK prompts.
- **DPA 2019** (`commercial_model.md §7.5`) — the §8 usage profiling for company-detection must be
  disclosed in terms; store the minimum.

---

## 11. Build order (incremental, each independently shippable) — ✅ ALL SHIPPED (2026-06-24)

1. ✅ **Tiers config + `entitlement_service`** (Redis primitive §2) + unit tests (reveal logic,
   quota edge, idempotent re-reveal, fail-safe-deny). → `services/billing_tiers.py`,
   `services/entitlement_service.py`, `tests/test_entitlement_service.py`.
2. ✅ **`geo_fuzz` + serializer gate** (§5) + tests (anon always fuzzed, deterministic blur,
   revealed user gets exact). → `services/geo_fuzz.py`, `tests/test_geo_fuzz.py`.
3. ✅ **`reveal` endpoint** (§6) wired to entitlement + metering event. → `routers/reveal.py`,
   `tests/test_reveal_endpoint.py`.
4. ✅ **Alembic tables** (§3) + **billing_service** STK initiate/callback/reconcile (§4) + the
   `checkout`/`callback`/poll endpoints. → `a1b2c3d4e5f6_billing_tables`, `services/billing_service.py`,
   `services/mpesa_client.py`, `services/billing_tasks.py`, `routers/billing.py`,
   `tests/test_billing_service.py` + `test_billing_tasks.py`. **Daraja sandbox** (`MPESA_ENV=sandbox`).
5. ✅ **Frontend** chooser modal + reveal flow. → `TierChooserModal`, `RevealContext`,
   `api/billing.ts` (+ `api/billing.test.ts`).
6. ✅ **Free hook tier** (§7) — in `entitlement_service.py`.
7. ✅ **Metering events + policy engine + usage-profile beat job** (§8) — the company-detection
   line. → `b2c3d4e5f6a7_metering_tables`, `models/metering.py`, `routers/metering.py`,
   `services/metering_service.py`, `services/policy_engine.py`, `services/policy_tasks.py`,
   `routers/policy.py`; FE `ProScaleModal` + `SoftGate` + `api/policy.ts`. Threshold 0.6, 30-day window.

> **Test/rollout note:** like P4a, build behind flags and prove the existing suites stay green;
> the STK path is sandbox-tested (AT/Daraja sandbox credentials) before any real shortcode. The
> serving/read app and the InSAR path are untouched.

---

## 12. Open parameters (decisions to tune, not blockers)

| Parameter | Default | Lever it controls |
|-----------|---------|-------------------|
| `FUZZ_RADIUS_M` | ~1000 m | accessibility vs how hard the exact pin is to sell (A/B) |
| Tier prices/quotas/windows | 20/3/2h · 50/6/4h · 100/10/24h | revenue vs adoption (`commercial_model.md §5`) |
| `HOOK_COOLDOWN` | 24 h | free-reveal generosity vs farming |
| Poll interval / timeout | 3 s / 60 s | "confirming payment" UX |
| Reconciliation cadence | ~2 min | lost-callback recovery latency |
| Company-score threshold | TBD | false-positive an engineer vs miss a bank (§8) |
| Window stacking | replace (not additive) | simplicity now; additive later if needed |

---

## 13. Relationship to the other docs

- **`commercial_model.md`** — the strategy (the *why*, the pricing, the access model). This doc
  implements its §3/§4/§5/§7/§11.
- **`work_flow.md`** — the system architecture, ports, and the InSAR integration (§9). The metering
  spine in §8 here is the one described in `work_flow.md §9.5`.
- **P4a** (`weespas-insar-p4a` memory) — the append-only/idempotent/Alembic-allow-list disciplines
  reused here for `payment_ledger` and the migration.
