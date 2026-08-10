"""Agent-comparison analytics: rank, funnel, per-listing benchmarks.

Reuses the cutoff parser and weight constants from analytics_service so the
numbers stay consistent with the rest of the dashboard.
"""
from __future__ import annotations

import statistics
from bisect import bisect_left
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from PE.weespas.models.analytics import PropertyViewEvent, Favorite
from PE.weespas.models.contact import ContactSubmission
from PE.weespas.models.property import Address, Agent, Property
from PE.weespas.services.analytics_service import (
    WEIGHT_FAVORITE, WEIGHT_INQUIRY, WEIGHT_VIEW, _since_dt,
)


# ===================== shared count helpers =====================

def _count_views(db: Session, cutoff, property_ids: Optional[list[str]] = None) -> int:
    q = db.query(func.count(PropertyViewEvent.id))
    if cutoff:
        q = q.filter(PropertyViewEvent.viewed_at >= cutoff)
    if property_ids is not None:
        if not property_ids:
            return 0
        q = q.filter(PropertyViewEvent.property_id.in_(property_ids))
    return q.scalar() or 0


def _count_favs(db: Session, cutoff, property_ids: Optional[list[str]] = None) -> int:
    q = db.query(func.count(Favorite.id))
    if cutoff:
        q = q.filter(Favorite.created_at >= cutoff)
    if property_ids is not None:
        if not property_ids:
            return 0
        q = q.filter(Favorite.property_id.in_(property_ids))
    return q.scalar() or 0


def _count_inqs(db: Session, cutoff, property_ids: Optional[list[str]] = None) -> int:
    q = db.query(func.count(ContactSubmission.id)).filter(
        ContactSubmission.property_id.isnot(None)
    )
    if cutoff:
        q = q.filter(ContactSubmission.created_at >= cutoff)
    if property_ids is not None:
        if not property_ids:
            return 0
        q = q.filter(ContactSubmission.property_id.in_(property_ids))
    return q.scalar() or 0


# ===================== AGENT RANK =====================

def compute_agent_rank(db: Session, agent_id: Optional[str], since: str = "30d") -> dict:
    cutoff = _since_dt(since)

    # Pull active listing counts per agent.
    active_q = (
        db.query(Property.agent_id, func.count(Property.id).label("active_listings"))
        .filter(Property.is_active.is_(True), Property.agent_id.isnot(None))
        .group_by(Property.agent_id)
    )
    active_by_agent: dict[str, int] = {row.agent_id: int(row.active_listings) for row in active_q}

    # Aggregated counts per (active) property in window.
    views_q = (
        db.query(PropertyViewEvent.property_id, func.count(PropertyViewEvent.id))
        .group_by(PropertyViewEvent.property_id)
    )
    if cutoff:
        views_q = views_q.filter(PropertyViewEvent.viewed_at >= cutoff)
    views_by_prop = {pid: int(c) for pid, c in views_q if pid}

    favs_q = (
        db.query(Favorite.property_id, func.count(Favorite.id))
        .group_by(Favorite.property_id)
    )
    if cutoff:
        favs_q = favs_q.filter(Favorite.created_at >= cutoff)
    favs_by_prop = {pid: int(c) for pid, c in favs_q if pid}

    inqs_q = (
        db.query(ContactSubmission.property_id, func.count(ContactSubmission.id))
        .filter(ContactSubmission.property_id.isnot(None))
        .group_by(ContactSubmission.property_id)
    )
    if cutoff:
        inqs_q = inqs_q.filter(ContactSubmission.created_at >= cutoff)
    inqs_by_prop = {pid: int(c) for pid, c in inqs_q if pid}

    # Map property -> agent (active only).
    prop_agent_q = (
        db.query(Property.id, Property.agent_id)
        .filter(Property.is_active.is_(True), Property.agent_id.isnot(None))
    )
    score_by_agent: dict[str, float] = {}
    for pid, aid in prop_agent_q:
        v = views_by_prop.get(pid, 0)
        f = favs_by_prop.get(pid, 0)
        i = inqs_by_prop.get(pid, 0)
        s = WEIGHT_VIEW * v + WEIGHT_FAVORITE * f + WEIGHT_INQUIRY * i
        if s == 0:
            score_by_agent.setdefault(aid, 0.0)
        else:
            score_by_agent[aid] = score_by_agent.get(aid, 0.0) + s

    # Pull agent names for everyone we care about.
    agent_ids = list(score_by_agent.keys())
    name_by_agent: dict[str, str] = {}
    if agent_ids:
        for a in db.query(Agent).filter(Agent.id.in_(agent_ids)).all():
            name_by_agent[a.id] = a.agent_name or "Unknown"

    rows = []
    for aid, score in score_by_agent.items():
        listings = active_by_agent.get(aid, 0)
        epl = (score / listings) if listings > 0 else 0.0
        rows.append({
            "agent_id": aid,
            "name": name_by_agent.get(aid, "Unknown"),
            "score": float(score),
            "engagement_per_listing": float(epl),
            "active_listings": int(listings),
        })

    rows.sort(key=lambda r: (r["engagement_per_listing"], r["score"]), reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    total = len(rows)
    epls = [r["engagement_per_listing"] for r in rows]
    p50 = p90 = 0.0
    if epls:
        try:
            quantiles = statistics.quantiles(epls, n=10) if len(epls) >= 2 else [epls[0]] * 9
            p50 = float(statistics.median(epls))
            p90 = float(quantiles[8]) if len(quantiles) >= 9 else float(epls[-1])
        except statistics.StatisticsError:
            p50 = float(epls[0])
            p90 = float(epls[0])

    me = None
    if agent_id:
        for r in rows:
            if r["agent_id"] == agent_id:
                me = r
                break
        if me is None and agent_id in active_by_agent:
            # Agent exists but has zero engagement — synthesize a row.
            agent_obj = db.query(Agent).filter(Agent.id == agent_id).first()
            me = {
                "agent_id": agent_id,
                "name": (agent_obj.agent_name if agent_obj else "You"),
                "score": 0.0,
                "engagement_per_listing": 0.0,
                "active_listings": int(active_by_agent.get(agent_id, 0)),
                "rank": total + 1,
            }

    leaderboard = [dict(r, is_me=(r["agent_id"] == agent_id)) for r in rows[:20]]
    if me and not any(r["agent_id"] == agent_id for r in leaderboard):
        leaderboard.append(dict(me, is_me=True))

    agent_block = None
    if me:
        rank = me["rank"]
        percentile = (1.0 - (rank - 1) / total) if total > 0 else 0.0
        agent_block = {
            "id": me["agent_id"],
            "name": me["name"],
            "rank": rank,
            "total": total,
            "percentile": round(percentile, 4),
            "score": me["score"],
            "engagement_per_listing": me["engagement_per_listing"],
            "active_listings": me["active_listings"],
        }

    return {
        "since": since or "all",
        "agent": agent_block,
        "platform": {"p50": round(p50, 2), "p90": round(p90, 2)},
        "leaderboard": leaderboard,
    }


# ===================== AGENT FUNNEL =====================

def compute_agent_funnel(db: Session, agent_id: Optional[str], since: str = "30d") -> dict:
    cutoff = _since_dt(since)

    def _rates(views: int, favs: int, inqs: int):
        v2f = (favs / views) if views > 0 else None
        f2i = (inqs / favs) if favs > 0 else None
        return v2f, f2i

    plat_views = _count_views(db, cutoff)
    plat_favs = _count_favs(db, cutoff)
    plat_inqs = _count_inqs(db, cutoff)
    plat_v2f, plat_f2i = _rates(plat_views, plat_favs, plat_inqs)
    platform = {
        "view_to_fav": round(plat_v2f, 4) if plat_v2f is not None else None,
        "fav_to_inq": round(plat_f2i, 4) if plat_f2i is not None else None,
    }

    agent_block = None
    if agent_id:
        prop_ids = [
            pid for (pid,) in db.query(Property.id).filter(
                Property.agent_id == agent_id, Property.is_active.is_(True)
            )
        ]
        a_views = _count_views(db, cutoff, prop_ids)
        a_favs = _count_favs(db, cutoff, prop_ids)
        a_inqs = _count_inqs(db, cutoff, prop_ids)
        a_v2f, a_f2i = _rates(a_views, a_favs, a_inqs)
        agent_block = {
            "views": a_views,
            "favorites": a_favs,
            "inquiries": a_inqs,
            "view_to_fav": round(a_v2f, 4) if a_v2f is not None else None,
            "fav_to_inq": round(a_f2i, 4) if a_f2i is not None else None,
        }

    return {
        "since": since or "all",
        "agent": agent_block,
        "platform": platform,
    }


# ===================== LISTING BENCHMARKS =====================

def _percentile_of(sorted_scores: list[float], value: float) -> float:
    """Position of value within sorted_scores in [0, 1] (no interpolation).

    sorted_scores must be ascending. Empty list → 0.0.
    """
    n = len(sorted_scores)
    if n == 0:
        return 0.0
    # Use bisect_left so equals don't inflate the percentile.
    idx = bisect_left(sorted_scores, value)
    return idx / n


def compute_listing_benchmarks(db: Session, agent_id: Optional[str], since: str = "30d") -> list[dict]:
    if not agent_id:
        return []
    cutoff = _since_dt(since)

    # Agent's active properties + their address county.
    rows = (
        db.query(Property.id, Property.title, Property.category_id, Property.listing_type, Address.county)
        .outerjoin(Address, Address.property_id == Property.id)
        .filter(Property.agent_id == agent_id, Property.is_active.is_(True))
        .all()
    )
    if not rows:
        return []

    out: list[dict] = []
    for prop_id, title, category_id, listing_type, county in rows:
        # Strict peer set: same category + county + listing_type, exclude self, active only.
        strict_q = (
            db.query(Property.id)
            .outerjoin(Address, Address.property_id == Property.id)
            .filter(
                Property.is_active.is_(True),
                Property.id != prop_id,
                Property.category_id == category_id,
                Property.listing_type == listing_type,
                Address.county == county,
            )
        )
        strict_ids = [pid for (pid,) in strict_q]
        peer_set_label = "category_county_type"

        if len(strict_ids) < 5:
            broad_q = (
                db.query(Property.id)
                .filter(
                    Property.is_active.is_(True),
                    Property.id != prop_id,
                    Property.category_id == category_id,
                    Property.listing_type == listing_type,
                )
            )
            broad_ids = [pid for (pid,) in broad_q]
            if len(broad_ids) >= 5:
                strict_ids = broad_ids
                peer_set_label = "category_type"
            else:
                # Compute self stats anyway for display.
                v = _count_views(db, cutoff, [prop_id])
                f = _count_favs(db, cutoff, [prop_id])
                i = _count_inqs(db, cutoff, [prop_id])
                score = WEIGHT_VIEW * v + WEIGHT_FAVORITE * f + WEIGHT_INQUIRY * i
                out.append({
                    "property_id": prop_id,
                    "title": title or "",
                    "score": float(score),
                    "views": int(v),
                    "favorites": int(f),
                    "inquiries": int(i),
                    "peer_set": "insufficient",
                    "peer_count": len(strict_ids),
                    "peer_median_views": None,
                    "peer_p90_views": None,
                    "percentile": None,
                })
                continue

        # Self counts.
        v = _count_views(db, cutoff, [prop_id])
        f = _count_favs(db, cutoff, [prop_id])
        i = _count_inqs(db, cutoff, [prop_id])
        score = WEIGHT_VIEW * v + WEIGHT_FAVORITE * f + WEIGHT_INQUIRY * i

        # Peer counts in one grouped pass each.
        pv_q = (
            db.query(PropertyViewEvent.property_id, func.count(PropertyViewEvent.id))
            .filter(PropertyViewEvent.property_id.in_(strict_ids))
            .group_by(PropertyViewEvent.property_id)
        )
        if cutoff:
            pv_q = pv_q.filter(PropertyViewEvent.viewed_at >= cutoff)
        peer_views_rows = {pid: int(c) for pid, c in pv_q}

        pf_q = (
            db.query(Favorite.property_id, func.count(Favorite.id))
            .filter(Favorite.property_id.in_(strict_ids))
            .group_by(Favorite.property_id)
        )
        if cutoff:
            pf_q = pf_q.filter(Favorite.created_at >= cutoff)
        peer_favs_rows = {pid: int(c) for pid, c in pf_q}

        pi_q = (
            db.query(ContactSubmission.property_id, func.count(ContactSubmission.id))
            .filter(ContactSubmission.property_id.in_(strict_ids))
            .group_by(ContactSubmission.property_id)
        )
        if cutoff:
            pi_q = pi_q.filter(ContactSubmission.created_at >= cutoff)
        peer_inqs_rows = {pid: int(c) for pid, c in pi_q}

        peer_view_counts: list[int] = []
        peer_scores: list[float] = []
        for pid in strict_ids:
            pv = peer_views_rows.get(pid, 0)
            pf = peer_favs_rows.get(pid, 0)
            pi = peer_inqs_rows.get(pid, 0)
            peer_view_counts.append(pv)
            peer_scores.append(WEIGHT_VIEW * pv + WEIGHT_FAVORITE * pf + WEIGHT_INQUIRY * pi)

        peer_scores.sort()
        peer_view_counts.sort()
        percentile = _percentile_of(peer_scores, score)

        median_views = float(statistics.median(peer_view_counts)) if peer_view_counts else None
        if len(peer_view_counts) >= 2:
            try:
                qs = statistics.quantiles(peer_view_counts, n=10)
                p90_views = float(qs[8])
            except statistics.StatisticsError:
                p90_views = float(peer_view_counts[-1])
        else:
            p90_views = float(peer_view_counts[-1]) if peer_view_counts else None

        out.append({
            "property_id": prop_id,
            "title": title or "",
            "score": float(score),
            "views": int(v),
            "favorites": int(f),
            "inquiries": int(i),
            "peer_set": peer_set_label,
            "peer_count": len(strict_ids),
            "peer_median_views": round(median_views, 1) if median_views is not None else None,
            "peer_p90_views": round(p90_views, 1) if p90_views is not None else None,
            "percentile": round(percentile, 4),
        })

    out.sort(key=lambda r: r["score"], reverse=True)
    return out
