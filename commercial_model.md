# commercial_model.md — Weespas + InSAR: revenue, pricing, and the access model

> **Status:** strategy of record, 2026-06-22. This is the *what* and *why* of how the
> platform makes money and stays free where it must. It is **not** an implementation spec
> (that is a separate billing-architecture doc, "B").
>
> **⚠️ Reconciliation note (2026-06-24): this layer is no longer greenfield — it SHIPPED.**
> The original status line ("no billing/quota/M-Pesa code exists yet") is now stale. The whole
> stack this doc specifies is implemented in Weespas: the O(1) Redis entitlement primitive
> (`services/entitlement_service.py`), the M-Pesa STK initiate/callback/reconcile flow
> (`services/billing_service.py` + `services/mpesa_client.py` + `services/billing_tasks.py`),
> the tier table (`services/billing_tiers.py`), the `reveal` + `billing` routers, server-side
> coordinate fuzzing (`services/geo_fuzz.py`), the §7 company-detection metering + policy engine
> (`routers/metering.py`, `services/metering_service.py`, `services/policy_engine.py`,
> `models/metering.py`), and two Alembic migrations (`a1b2c3d4e5f6_billing_tables`,
> `b2c3d4e5f6a7_metering_tables`). Frontend: `TierChooserModal`, `ProScaleModal`, `SoftGate`,
> `RevealContext`, `api/billing.ts`, `api/policy.ts`. M-Pesa runs against the Daraja **sandbox**
> today (`MPESA_ENV=sandbox`); the locked *strategy* below is otherwise built as written. The
> buildable detail and per-file map live in `billing_architecture.md`.
>
> **Scope:** the whole **Weespas** application (backend + `weespas-frontend`) is the identity,
> permissions, and **revenue** plane. **InSAR** is a free, high-value service we give away as a
> customer-acquisition cost (CAC). See `work_flow.md §9` for the technical integration map and
> the four-codebase architecture this sits on top of.
>
> **All Kenya figures below are sourced (see §10), not invented.** Where a real comparable
> doesn't exist yet (e.g. the enterprise per-asset price), the doc states the *structure* and
> flags the number as TBD rather than fabricating it.

---

## 1. The thesis in one paragraph

People in Nairobi pay **1,000–3,000 KES to a human agent just to be *shown* a house** — a fee
that is **non-refundable** and is the single biggest house-hunting **scam vector** in the city
(pay, get shown one derelict unit, agent disappears). Weespas removes that pain: search verified
listings by **kilometre radius**, see them, and pay a **tiny fraction** only at the moment you
want to *act* on a location (get directions / open the map of pings). Acquisition is powered by
**InSAR** — a genuinely useful, free "is my building sinking?" risk map — because the person
anxious about their building is the *same* geolocated person who rents, owns, moves house, or
needs a structural engineer. The acquisition artifact (a geolocated building) **is** the commerce
key, on the same building-footprint geometry the listings already use.

---

## 2. Two products, two roles

| | **Weespas** (backend + `weespas-frontend`) | **InSAR** (backend + frontend) |
|---|---|---|
| Role in the business | **Revenue + identity + permissions plane** | **Free CAC / top-of-funnel service** |
| What it sells | House-location access (tenant) + success commission (supply) | Nothing to individuals; data licences to companies |
| Who it serves | Renters, owners, agents, staff, admin | Everyone, free — owners, tenants, engineers, citizens |
| Why it's structured this way | Owns the user, the session, the money | Stays stateless; rides Weespas identity where needed |

InSAR is **deliberately not** a revenue centre for ordinary people. Its job is to demonstrate
value so visceral ("my building's risk, free, on a map") that it pulls users into Weespas, where
the housing transaction happens. See §7 for where InSAR *does* earn (companies only).

---

## 3. The access model — free by default, pay only to *act*

**Core principle: maximise accessibility. The platform is free to browse. Payment is prompted
only at the moment of high-intent action, never on page load.**

This is the most important product decision in the model, so it is precise:

### 3.1 What is always free (no prompt, ever)
- Loading the site; browsing the **gallery** (images / short videos) of listings.
- Searching by radius, filtering, scrolling the feed, watching listing shorts.
- Viewing a listing's details, photos, price, description.
- **Opening Map view and seeing the *approximate* spread of listings** — markers snapped to a
  neighbourhood-blob, not the exact door (see §3.4). The user sees *"8 homes near you"* as a
  cluster; what's withheld is the **precise, navigable pin**, not the existence of the homes.
- All of InSAR's risk map for individuals (see §6).

Page load **never** shows a payment prompt. **Map view never shows a payment prompt on its own.**
Friction at the door kills a CAC funnel; we keep the door — and the map — wide open. Only one
thing is paid: turning an approximate marker into an **exact, navigable location**.

### 3.2 The paid action is a single primitive: **reveal**

There is exactly **one** paid action, reached from two places. We do *not* charge for "entering
the map" or "pressing a button" — we charge for **revealing the precise location of one specific
listing**. Call this a **reveal**. A paid window buys a **quota of reveals** (the "locations" in
the ladder) plus a **time window**.

The two surfaces that trigger a reveal:

1. **Tapping a marker on the Map** to get its exact pin. `weespas-frontend` already has the
   gallery⇄map `ViewToggle` (`src/App.tsx:282`) and the ping map (`PropertyMap`,
   `src/App.tsx:347`). The map itself is free (fuzzy); **tapping a pin to sharpen it = a reveal.**
2. **"Get directions"** on a listing's details page (new button — §4). Navigating to that unit
   needs its exact location = **a reveal of that listing.**

Both are the **same** server call against the **same** per-window set of revealed listing-IDs
(§3.3). So a listing you unlocked on the map is *already* unlocked for directions, and vice
versa — never double-charged.

### 3.3 How "which houses does the user want?" dissolves

The earlier open question — *"if a window buys 3 locations, how do we know which 3 the user
wants?"* — has a clean answer: **we don't predetermine them. The user reveals the ones they want,
one tap at a time, and each distinct reveal fills one slot.** We cap the *size* of the set (the
quota) and its *lifetime* (the window); the taps fill it in.

- A reveal of an **already-revealed** listing within the window is **free and idempotent** (you
  can re-open directions to a place you already paid to see).
- The entitlement is just *"the set of distinct listing-IDs revealed in this window, ≤ N."*
  Counting is trivial and identical on both surfaces — which removes the asymmetry where
  directions felt easy to count but the map felt hard.

### 3.4 Why the exact location must be a server-gated secret

The exact lat/lon **is the paid good**, so it must never reach the browser until it's paid for —
otherwise anyone reads it from the network tab and the whole model leaks for free. This is a
**trust boundary**, the same discipline as the InSAR flag export:

- Feed/map endpoints return **fuzzed coordinates** (snapped/jittered within a blob) for any
  listing the user hasn't revealed.
- A single `POST /listings/{id}/reveal` endpoint is the **only** thing that returns exact coords,
  and it is gated by the entitlement check.
- Bonus: this **kills address-scraping** — a scammer can't vacuum every exact address for 20 bob;
  they'd have to burn a reveal-slot per listing.

**The fuzz radius is a tunable lever** (a ~1 km neighbourhood blob makes the exact pin clearly
worth buying; a 100 m blob might be "good enough" and depress conversion). Ship a sensible
default and A/B it.

### 3.5 Why reveal and not "view details"

Details are **discovery** (free — how they fall in love with a unit). A precise, navigable
location is **fulfilment** (paid — how they physically get there). We charge at fulfilment, never
at discovery — exactly mirroring the market: agents don't charge to *describe* a house, they
charge to *take you to it*. We make "take you to it" cost 20–100 KES instead of 1,000–3,000, and
remove the scam.

---

## 4. The two reveal surfaces — how they function

A reveal (§3.2) is reached from the Map and from a new "Get directions" button. Both behave
identically because both call the same gated `reveal(listing_id)` (§11).

### 4.1 Pop-up timing — **free fuzzy map, prompt on first reveal (locked)**

When the user toggles to **Map view**, we do **not** pop the chooser immediately. We show the
**free, fuzzy** map — a cluster of real homes near them. The chooser appears only when they
**tap a pin to sharpen it** (their first reveal with no active window).

> *Decision (locked):* this is option **(ii)** over option (i) "pop-up on Map click." A map
> showing 8 real homes nearby is the **best possible advertisement** for "unlock to go see them"
> — a *warm* ask after the user has seen what they'd get, versus a *cold* ask on a blank intent.
> It also honours "maximise accessibility" literally: nothing is walled except the exact pin. The
> Map button still leads to the chooser — just one tap later, at the moment of real intent.

### 4.2 Behaviour of a reveal (Map pin tap **or** Get directions)
- **No active window** → opens the **subscription pop-up** (20/50/100 chooser, §5). On successful
  M-Pesa payment → a window is granted → the reveal completes immediately (exact pin / directions
  open).
- **Active window, listing not yet revealed, quota left** → **consumes one slot** (`SADD` to the
  revealed set), returns exact location.
- **Active window, listing already revealed** → **free, idempotent** — returns exact location
  again, no slot consumed (re-open directions to a place you already paid for).
- **Active window, quota used up** → soft message: *"You've unlocked all N locations in this
  window — upgrade for more,"* re-opening the chooser. Never an error, never a dead end.

### 4.3 What "Get directions" is (MVP → later)
- **MVP:** on reveal, drop the precise pin + hand off to the device's own map app (Google Maps /
  `geo:` intent) with the listing's lat/lon as destination. **Zero mapping cost to us**; the
  phone navigates.
- **Later:** in-app directions from the user's location, ETA, and (Phase 2) one-tap *"book a
  boda/taxi to go view this house"* — reusing the same geo + M-Pesa + booking rails.

### 4.4 Where the button lives
A new affordance on the listing **card** and the `PropertyDetails` panel. Because details are
free (discovery), the button is always *visible*; pressing it is what triggers the reveal flow
above. This keeps the funnel honest: browse freely, pay only to navigate.

---

## 5. Pricing — the ladder, the math, and why it's right

### 5.1 The tiers (kept from the founder's proposal; **adjustable, not set in stone**)

| Tier | Price | Locations | Window |
|------|-------|-----------|--------|
| Hook (free) | 0 KES | 1 | ~30 min, small radius |
| 1 | **20 KES** | 3 | 2 hours |
| 2 | **50 KES** | 6 | 4 hours |
| 3 | **100 KES** | 10 | 24 hours |

A "location" = one property you reveal-on-map / get-directions-to. A "window" = the time the
entitlement stays active. Both map/pings and directions consume from the **same** location
counter (§4).

### 5.2 The pricing math — sell *time*, guardrail with *locations*

Naïvely, price-per-location *rises* across tiers (6.7 → 8.3 → 10 KES/location), which looks
backwards for a volume discount. It isn't — because **the product is time, and locations are a
guardrail against abuse**. Priced per **location-hour**, it's a clean 8× volume discount:

| Tier | Price ÷ (locations × hours) | **Per location-hour** |
|------|------------------------------|------------------------|
| 1 | 20 / (3 × 2) | **3.33 KES** |
| 2 | 50 / (6 × 4) | **2.08 KES** |
| 3 | 100 / (10 × 24) | **0.42 KES** |

**Marketing implication:** lead with **Tier 3 as the hero** — *"a full day of house-hunting for
100 bob"* — not with a per-location count. Tier 1 is the impulse entry; Tier 3 is where the
value-per-shilling is obvious.

### 5.3 Willingness-to-pay reality (why we are *under*-pricing, deliberately, for now)

- Incumbent: human agent **1,000–3,000 KES to locate one house**, non-refundable, scam-prone.
  [Standard], [Tenantt]
- Our Tier 3: **100 KES for 10 verified listings over 24h** = a **10–300× price drop** that also
  **removes the scam** (you pay *us*, a platform with verified listings, not a stranger who
  vanishes).
- **The real commercial risk is therefore under-monetisation, not price resistance.** Cheap is
  the *correct* choice during the CAC land-grab — we want ubiquity first. Headroom for later:
  escorted verified viewings, priority/featured listings, premium radius, agent lead-gen.

### 5.4 The two-sided revenue split (decision, locked)

Do **not** charge the tenant twice. Revenue comes from two different parties for two different
values:

| Side | Pays | For | Rationale |
|------|------|-----|-----------|
| **Demand (tenant)** | The small access fee (20/50/100) | Locating / directions | Anti-scam, low-friction, CAC monetisation |
| **Supply (landlord/agent)** | A **success commission** on a **closed** deal | A tenant actually placed | Small vs the one-month-rent / up-to-25%-of-first-month agents already extract [Avenue] |

The tenant fee is tiny and frequent (impulse). The supply commission is larger and rare (on
conversion). Together they avoid nickel-and-diming the tenant while capturing the real value at
the point of a completed match.

---

## 6. InSAR free/paid line (for individuals vs companies)

**Locked split:**

- **Free, forever, for individuals** — unlimited interactive map browsing; look up *any* single
  building's risk visually; pull the full detailed risk report on **~5–10 buildings per month**.
  Every owner, tenant, engineer, journalist, and curious citizen lives entirely inside this and
  **never hits a wall.**
- **Paid line triggers only on commercial-shaped actions:** bulk lookups (> N buildings), **API
  access**, large CSV / report **exports**, **portfolio watchlists with alerts** (monitoring >
  N assets).

A bank **cannot** do mortgage-book risk analysis inside the free envelope — the moment they try
(bulk, API, export, portfolio), they cross into the paid line. An individual never goes near it.
This is the same segmentation GitHub/LinkedIn/data-vendors use: meter the *business-shaped
actions* (seats, API, bulk), not declared identity.

---

## 7. Company detection — meter behaviour, never self-declaration

**The trap:** "free for people, paid for companies" fails if we ask, because a bank analyst will
simply click "individual." **The fix: stop detecting *identity*; meter *behaviour*** — price the
actions only a company performs, and shape the free tier so an individual never reaches the wall
while a company inevitably does.

### 7.1 What a company *does* that a person never does

| Dimension | Individual (owner / tenant / engineer) | Company (bank / insurer) |
|-----------|----------------------------------------|--------------------------|
| Volume | 1 building → tens (engineer's contracts) | **thousands** (the loan book) |
| Breadth | one area | systematic sweep across AOIs |
| Access mode | clicks a map | wants **API / CSV / bulk / portfolio** |
| Output | looks; maybe one report | feeds underwriting models |
| Cadence | occasional | regular, automated, portfolio-wide |

The metering line is **volume + access-mode**, never a declared label.

### 7.2 The commercial-likelihood score (actuarial-style, soft)

Computed from the **existing Weespas session/telemetry spine** (`user_sessions` + behavioural
event tables — see `work_flow.md §9.5`). Weighted signals, not a hard rule:

1. **Volume** — distinct buildings / detailed-views / exports per rolling window.
2. **Breadth** — distinct AOIs swept; portfolio-sweep coverage pattern.
3. **Automation** — request regularity; API-key usage; headless / off-hours batch.
4. **Account** — corporate email domain (e.g. `kcbgroup`, `equitybank`, `britam`, `jubilee`);
   many seats sharing one billing identity.
5. **Output** — who downloads CSV / reports vs who just looks.

Above threshold → a **soft gate**, never an accusation:
> *"You're using Weespas Risk at a professional scale — here's a business plan."*

### 7.3 Exemptions enforced by *structure*, not honesty

- **Engineers / professionals → free, by *vetted role*.** This is *why* the `professional` and
  `authority` roles sit behind the certification / role-application **review** flow (already
  built in P4a). A bank analyst **cannot self-grant** `professional` — it's reviewed. "Engineers
  are free" is enforced by a credential, not a checkbox.
- **Owners → free, by *structure*.** Checking *your own* building is single-building, low-volume
  → free by construction. To claim the `property_owner` benefit, tie it to a **verified building
  link** (claimed listing / title / utility match), not a self-declared flag.
- **Staff / admin → role-based**, trivial.
- **Ordinary users / tenants → free** inside the generous individual envelope.
- **Net:** the *only* parties who pay are those whose usage is **commercial-scale AND who lack a
  free role** — precisely banks and insurers, and nobody else.

### 7.4 The pricing principle that makes evasion pointless

Set the business price **below the company's cost-to-evade and cost-to-build-it-themselves.** If
a licence costs less than the engineering effort to scrape around the gate, **procurement just
pays.** The defensible, tamper-evident record (next section) is worth far more to them than the
effort of dodging a fee.

### 7.5 Legal note (Kenya Data Protection Act 2019)

Profiling usage to bill touches the DPA. Usage-metering for billing is defensible
(contractual / legitimate-interest), **but** the corporate-detection behaviour must be
**disclosed in the terms**, and we store the **minimum** needed. A terms clause, not a blocker.

---

## 8. Enterprise pricing — "assets monitored" is the core production number

**Locked:** the central business metric is **assets monitored**. Keep two senses separate:

- **Coverage (our north-star supply metric):** total distinct buildings under active InSAR
  monitoring. *This is what we grow.*
- **Assets-under-monitoring *per enterprise customer* (the billing unit):** the buildings in
  *that bank's / insurer's* portfolio watchlist with alerts. **This is what we invoice** —
  per-monitored-asset, **data-licence style**, negotiated, **pilot-first**.

**Lead the enterprise pitch with the tamper-evident record, not the map.** The hash-chained
structural-flag + `notification_audit` chain (built in P4a) is **defensible, timestamped risk
provenance** — exactly what an underwriter or actuary needs to put risk on the books. Banks and
insurers buy *provenance they can defend in a dispute* far more readily than a visualisation.

**Pricing structure (number is TBD — needs a real comparable):** annual platform fee + per-
monitored-asset-per-month + API volume, comparable to how **Metropol / TransUnion** sell credit-
reference feeds in Kenya. We will not commit a KES figure until we have a real comparable or a
pilot quote; the *structure* (per-asset data licence) is what's locked.

---

## 9. Marketplace — explicit Phase 2 (the bigger vision)

Movers, taxis, boda-bodas, and shops are **Phase 2**, after the house-search + InSAR-CAC loop is
proven. They reuse the **identical** geo-radius + M-Pesa + booking rails.

**Why it's coherent:** moving house is a **life-event bundle** — find house → book a mover →
transport (taxi / boda) → furnish (shops). Weespas can own that whole moment.

**The moat thesis (the strongest strategic idea in the vision):** social media optimises
**attention** — a follower graph is right for *entertainment* and wrong for *trade*, because it
taxes every small seller with a cold-start problem (no following → no discovery → no sales).
Weespas's **geo-radius search is proximity-and-intent-native**: *"get discovered instantly, no
following required — just sell."* That is a real, defensible difference. "Every house a shop" is
the platform end-state; the MVP is house-search revenue first.

**Sequencing (locked):** prove house-search + InSAR-CAC → then movers/transport (same rails) →
then shops/"every house a shop."

---

## 10. The Kenya ground-truth this model rests on

| Fact | Figure | Why it matters | Source |
|------|--------|----------------|--------|
| M-Pesa "Kadogo" zero-rating | Transactions **≤ 100 KES are free to the customer** (P2P / PayBill / Buy Goods) | Our 20/50/100 tiers cost the **user** nothing in fees | [TechCabal], [Techweez] |
| Buy Goods merchant tariff | **≤ 200 KES is free to the merchant**; above: 0.55% capped at 200 | Our tiers cost **us** ~nothing in fees → micropayments are viable *here* in a way they aren't in most markets | [TechCabal] |
| Agent locating/viewing fee | **1,000–3,000 KES per house, non-refundable** | The incumbent price we undercut 10–300× | [Standard], [Tenantt] |
| Agent commission norm | Up to **~25% of first month / one month's rent** | Anchors our supply-side success commission as small by comparison | [Avenue] |
| Nairobi rent (bedsitter / 1BR) | Bedsitter **12k–20k** (5.5k–8k Eastlands), 1BR **20k–45k** | Sizes the market and what a tenant can spare on a locating fee | [Skyline], [Money254] |
| Daraja API access | The **API itself is free**; only the underlying tariffs apply | No platform fee to integrate M-Pesa | [TechCabal] |

**Sources:**
[TechCabal]: https://techcabal.com/2025/10/13/m-pesa-charges-in-kenya-2025/
[Techweez]: https://techweez.com/2024/12/30/m-pesa-charges-withdrawal-and-send-money-fees-2025/
[Standard]: https://www.standardmedia.co.ke/business/home-away/article/1144001429/do-not-pay-viewing-fee-before-seeing-a-house
[Tenantt]: https://tenantt.co.ke/common-house-hunting-scams-in-nairobi/
[Avenue]: https://avenuepropertycenterkenya.com/real-estate-agency-fees-in-kenya-what-you-need-to-know/
[Skyline]: https://theskylinecollection.com/how-much-does-rent-cost-in-nairobi-a-complete-2025-26-guide/
[Money254]: https://www.money254.co.ke/post/cost-of-bedsitters-one-bedroom-in-nairobi-estates-news-and-analysis

---

## 11. Payment mechanics summary (detail lives in the billing-architecture doc)

- **Direct M-Pesa STK Push, pay-before-access. One payment buys a tier-window** (not per-search —
  a PIN prompt per 20-bob search is too heavy given STK's 5–30 s round-trip).
- **No wallet / no stored balance.** A wallet would make us hold customer **float** → pulls us
  toward CBK **Payment Service Provider / e-money licensing** + refund & reconciliation burden.
  Direct pay-per-tier holds **zero float**. And under the Kadogo / Buy Goods bands the fee saving
  a wallet would give is **zero** anyway — so the wallet has no upside and real downside.
- **We already have verified phone numbers** (OTP signup) → STK targeting is frictionless.
- **The one risk to engineer carefully — debit-but-callback-fails:** M-Pesa takes the money but
  the confirmation doesn't reach us. Mitigation: **idempotent reconciliation keyed on the M-Pesa
  transaction id**, a "confirming payment…" UI state, and a reconciliation poll. Known, solved
  pattern; flagged here so it's never forgotten.
- **Entitlement = a reveal-quota + a window, all O(1) in Redis.** A window buys *N reveals* for
  *T seconds*. Two keys per user, both expiring with the window (no cleanup job):
  ```
  ent:{user}:window   → { tier, quota:N }     TTL = window seconds   (2h ⇒ 7200)
  ent:{user}:unlocked → SET of listing_ids    TTL = window seconds
  ```
  `reveal(listing_id)`:
  1. `EXISTS window`? no ⇒ **show chooser** (no active window).
  2. `SISMEMBER unlocked, id`? yes ⇒ return exact coords (**free, idempotent** — no re-charge).
  3. `SCARD unlocked < N`? no ⇒ quota spent ⇒ **offer upgrade**.
  4. `SADD unlocked, id` ⇒ return exact coords.

  Every step is O(1) (`EXISTS`/`SISMEMBER`/`SCARD`/`SADD`); the TTL auto-clears the unlocked set
  when the window ends. This maps the ladder exactly: *"20 KES = 3 listings / 2h"* ⇒
  `quota=3, TTL=7200`. The Map pin-tap and Get-directions both call this same function against the
  same `unlocked` set, so a listing is never charged twice across the two surfaces.
- **Exact coordinates are server-gated (a trust boundary).** Feed/map endpoints return **fuzzed**
  coords for un-revealed listings; only `POST /listings/{id}/reveal` returns exact coords, behind
  the entitlement check. This is what makes the free fuzzy map safe to ship and kills bulk
  address-scraping. The fuzz radius is a tunable conversion lever (default ~1 km, A/B it).
- **The ledger of payments is append-only, idempotent on M-Pesa transaction id** — same discipline
  as the `notification_audit` hash-chain.

---

## 12. Locked decisions (index)

1. **Revenue:** two-sided — tenant access fee + supply success commission. Don't double-charge.
2. **Payment:** direct M-Pesa STK, pay-per-tier-window, **no wallet** (zero float, zero fees).
3. **Access model:** free to browse — incl. a **free fuzzy Map** (neighbourhood-blob markers).
   The one paid action is a **reveal** (sharpen one listing's exact, navigable location), reached
   by **tapping a map pin** or **Get directions**. Both draw from one per-window reveal-set, so a
   listing is never double-charged. Pop-up fires on the **first reveal** (option ii), **never on
   page load and never on entering Map view**. A window = N reveals for T hours; re-revealing an
   already-unlocked listing is free. Exact coords are **server-gated** (fuzzed until revealed).
4. **InSAR free line:** unlimited browsing + ~5–10 reports/mo for individuals; paid only for
   bulk / API / export / portfolio.
5. **Company detection:** meter **behaviour**, never self-declaration; exemptions enforced by
   **vetted role / verified structure**; soft gate, not accusation; DPA-disclosed.
6. **Enterprise:** priced on **assets monitored** (per-asset data licence, pilot-first, KES TBD);
   **lead with the tamper-evident audit record**.
7. **Marketplace:** explicit **Phase 2**; reuses geo + M-Pesa + booking rails; prove house-search
   + InSAR-CAC first.

> **Next doc (B):** the billing-and-entitlements **architecture** — STK Push flow, the idempotent
> C2B reconciliation ledger, the O(1) Redis entitlement grant, the metering events, and the
> policy engine (`require_role` + usage-score gate → free / metered / blocked). This document is
> the *strategy*; doc B is the *buildable spec*.
