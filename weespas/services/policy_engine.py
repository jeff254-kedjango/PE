"""The §8 policy engine — `gate(user, action) -> Decision`.

billing_architecture.md §8.2. This governs the SECOND revenue line: InSAR commercial
use by companies (banks, insurers). It is SEPARATE from the per-reveal entitlement
(§2, which monetises individual house-hunting). Two revenue lines, one telemetry spine.

The decision combines two checks that already exist in design:
  1. require_role exemption — staff/admin/professional/authority/property_owner are
     vetted free roles for InSAR commercial actions (commercial_model.md §7.3). A role
     can't be self-granted (it's behind cert/role-application review), so the exemption
     is enforced by STRUCTURE, not honesty.
  2. commercial-likelihood score — precomputed offline into UserUsageProfile by the
     beat job (`policy_tasks.recompute_usage_profiles`). The gate reads ONE row (PK
     lookup) so it stays O(1) on the request path; the threshold lives in the job, not
     here (UserUsageProfile.is_metered is the precomputed verdict).

The gate NEVER returns "blocked" for a real user action — a high score yields a SOFT
"metered" decision (an upsell), never an accusation. `blocked` exists in the vocabulary
only as a future hard-stop knob; nothing in §8 emits it.

This module also owns the pure scoring function so the beat job and the tests share one
definition of "what makes a user look like a company".
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.models.user import User, UserRole
from PE.weespas.models.metering import (
    MeteringEvent, UserUsageProfile,
    COMMERCIAL_EVENT_ACTIONS, EVENT_INSAR_EXPORT, EVENT_INSAR_BUILDING_VIEW,
    EVENT_INSAR_BUNDLE_FETCH,
)

logger = logging.getLogger(__name__)


class Decision(str, enum.Enum):
    FREE = "free"        # within the generous individual envelope, or a vetted role
    METERED = "metered"  # professional-scale use → soft upsell to a business plan
    BLOCKED = "blocked"  # reserved hard-stop; §8 never emits this for a user action


# Roles that are exempt from the commercial gate (vetted → always free for InSAR use).
# A bank analyst cannot self-grant any of these (they sit behind review), so the
# exemption can't be gamed by clicking "I'm an engineer".
_EXEMPT_ROLES = frozenset({
    UserRole.STAFF.value, UserRole.ADMIN.value,
    UserRole.PROFESSIONAL.value, UserRole.AUTHORITY.value,
    UserRole.PROPERTY_OWNER.value,
})


@dataclass(frozen=True)
class ScoreBreakdown:
    """The commercial-likelihood score and the signals behind it (for transparency
    and the soft-gate copy)."""
    score: float            # [0,1]
    volume: int             # commercial actions in window
    breadth: int            # distinct AOIs swept
    export_count: int       # CSV/report exports
    automation: float       # request-regularity proxy [0,1]
    corporate_domain: bool  # known corporate email domain


def _has_exempt_role(user: User) -> bool:
    roles = set(user.roles or [])
    if not roles and getattr(user, "role", None) is not None:
        roles = {user.role.value}
    return not roles.isdisjoint(_EXEMPT_ROLES)


def _corporate_domain(email: Optional[str]) -> bool:
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return domain in settings.company_domain_set


def compute_score(
    db: Session,
    user: User,
    *,
    now: Optional[datetime] = None,
) -> ScoreBreakdown:
    """Compute a user's commercial-likelihood score over the rolling window.

    Weighted blend of five signals (commercial_model.md §7.2), each normalised to
    [0,1] then combined. Deliberately simple + explainable (this is an actuarial-style
    nudge, not an ML model): volume, breadth, exports, automation, corporate domain.
    O(events in window) — runs OFFLINE in the beat job, never on the request path.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.company_score_window_days)

    rows = (
        db.query(MeteringEvent.action, MeteringEvent.aoi_code, MeteringEvent.created_at)
        .filter(MeteringEvent.user_id == user.id, MeteringEvent.created_at >= cutoff)
        .all()
    )

    commercial = [r for r in rows if r.action in COMMERCIAL_EVENT_ACTIONS]
    volume = len(commercial)
    breadth = len({r.aoi_code for r in rows if r.aoi_code})
    export_count = sum(1 for r in rows if r.action == EVENT_INSAR_EXPORT)

    # Automation proxy: how machine-like the access pattern is. A human house-hunter is
    # bursty and clicks; a batch job views in bulk and — the strongest tell — pulls whole
    # AOI bundles from the data API directly (a human never curls a bundle). We approximate
    # with the count of bulk InSAR views PLUS bundle fetches over the volume-saturation
    # scale. Counting bundle_fetch here (not only in volume/breadth) is what lets a pure
    # server-side scraper — which emits no clicks at all — still cross the metered threshold.
    insar_machine_pulls = sum(
        1 for r in rows
        if r.action in (EVENT_INSAR_BUILDING_VIEW, EVENT_INSAR_BUNDLE_FETCH)
    )
    automation = min(1.0, insar_machine_pulls / max(1, settings.company_volume_saturation))

    corporate = _corporate_domain(getattr(user, "email", None))

    # Normalise each signal to [0,1].
    v = min(1.0, volume / max(1, settings.company_volume_saturation))
    b = min(1.0, breadth / max(1, settings.company_breadth_saturation))
    e = min(1.0, export_count / max(1, settings.company_export_saturation))
    a = automation
    c = 1.0 if corporate else 0.0

    # Weighted sum (weights sum to 1.0). Volume + breadth dominate; corporate domain
    # is a meaningful but non-decisive nudge; exports + automation refine.
    score = 0.30 * v + 0.25 * b + 0.15 * e + 0.15 * a + 0.15 * c
    score = max(0.0, min(1.0, score))

    return ScoreBreakdown(
        score=score, volume=volume, breadth=breadth,
        export_count=export_count, automation=a, corporate_domain=corporate,
    )


def gate(db: Session, user: Optional[User], action: str) -> Decision:
    """The request-path decision: free / metered / blocked. O(1) — one PK lookup.

    - Anonymous or no user → FREE (they live in the generous individual envelope; the
      reveal line already gates anything that costs us money).
    - Vetted exempt role → FREE (structural exemption).
    - Otherwise read the precomputed UserUsageProfile: is_metered ⇒ METERED, else FREE.
      We never compute the score here (that's the beat job) so the gate is constant-time.
    """
    if user is None:
        return Decision.FREE
    if _has_exempt_role(user):
        return Decision.FREE
    profile = db.get(UserUsageProfile, user.id)
    if profile is not None and profile.is_metered:
        return Decision.METERED
    return Decision.FREE


def upsert_profile(db: Session, user: User, breakdown: ScoreBreakdown) -> UserUsageProfile:
    """Write/refresh a user's UserUsageProfile from a freshly-computed breakdown.

    `is_metered` is decided HERE (the one place the threshold lives) so the request-path
    gate is a pure lookup. A vetted-exempt user is never marked metered (no point — the
    gate would exempt them anyway; keeping the flag honest avoids a misleading UI chip)."""
    metered = (
        breakdown.score >= settings.company_score_threshold
        and not _has_exempt_role(user)
    )
    profile = db.get(UserUsageProfile, user.id)
    if profile is None:
        profile = UserUsageProfile(user_id=user.id)
        db.add(profile)
    profile.score = breakdown.score
    profile.is_metered = 1 if metered else 0
    profile.volume = breakdown.volume
    profile.breadth = breakdown.breadth
    profile.export_count = breakdown.export_count
    profile.automation = breakdown.automation
    profile.corporate_domain = 1 if breakdown.corporate_domain else 0
    db.commit()
    return profile
