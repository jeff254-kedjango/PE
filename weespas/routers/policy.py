"""Policy / company-detection read API (billing_architecture.md §8.2).

One endpoint: the signed-in user's own gate status, for the soft-gate UI ("you're
using Weespas Risk at a professional scale — here's a business plan"). This is the
ONLY place the company-detection verdict surfaces to a client, and only ever for the
caller themselves. It is informational: the verdict is a soft upsell, never a block.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.models.user import User
from PE.weespas.models.metering import UserUsageProfile
from PE.weespas.services.auth_service import get_current_user
from PE.weespas.services import policy_engine

router = APIRouter(prefix="/policy", tags=["policy"])


@router.get("/me")
def my_policy(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """The caller's commercial-use status. `decision` drives the soft-gate banner;
    the breakdown lets the UI say *why* ("you swept N AOIs / exported N reports")."""
    decision = policy_engine.gate(db, user, action="insar_building_view")
    profile = db.get(UserUsageProfile, user.id)
    if profile is None:
        return {"decision": decision.value, "metered": False, "score": 0.0}
    return {
        "decision": decision.value,
        "metered": bool(profile.is_metered),
        "score": round(profile.score, 3),
        "signals": {
            "volume": profile.volume,
            "breadth": profile.breadth,
            "export_count": profile.export_count,
            "automation": round(profile.automation, 3),
            "corporate_domain": bool(profile.corporate_domain),
        },
    }
