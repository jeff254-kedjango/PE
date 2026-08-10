"""Shop-ranking access entitlement (§8, Chunk B) — the paywall stub for radius > 200 km.

A row in ``ranking_entitlements`` grants the owning user (weespas ``sub``) access to the
"any radius" ranking view until ``expires_at``. Two ``kind`` values are supported:

  * ``one_time_2h`` — a 2-hour token, presumably purchased on demand.
  * ``annual``     — a rolling 12-month subscription.

The payment integration is DEFERRED (this increment). Rows are created today by admin scripts
or tests; the frontend renders a paywall CTA when the ranking endpoint reports
``paywall_required=True``. This model exists so the endpoint + FE can be end-to-end wired
against a real gate; the checkout flow will slot in behind the same table without a schema
change.

Not a cross-DB FK (``user_uuid`` is the token ``sub``, same discipline as every other
commerce table). Query pattern: newest row per user whose ``expires_at`` is in the future.
Index ``ix_ranking_entitlements_user_expires`` (user_uuid, expires_at DESC) makes that a
single index seek.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.sql import func

from PE.commerce.core.database import Base, utcnow


# Legal `kind` values. Kept as string constants (not a DB enum) so a future kind can be added
# without an ALTER — the service is the authority on which values are accepted at write time.
ENTITLEMENT_KIND_ONE_TIME_2H = "one_time_2h"
ENTITLEMENT_KIND_ANNUAL = "annual"
ENTITLEMENT_KINDS = (ENTITLEMENT_KIND_ONE_TIME_2H, ENTITLEMENT_KIND_ANNUAL)


class RankingEntitlement(Base):
    """A time-bounded grant to see ranking data at radii > 200 km."""
    __tablename__ = "ranking_entitlements"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    # The buying user (weespas token `sub`). NOT a cross-DB FK.
    user_uuid = Column(String, nullable=False, index=True)
    # 'one_time_2h' | 'annual' (see ENTITLEMENT_KINDS). Enforced in the service, not by a DB
    # CHECK constraint (adding a new kind should not require a migration).
    kind = Column(String(16), nullable=False)
    # The moment the grant lapses. The service treats a row as active iff expires_at > now.
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (
        # Matches the additive DDL: newest-first "is there an active row for this user" probe is
        # an index range scan over (user_uuid = ?, expires_at > now()).
        Index("ix_ranking_entitlements_user_expires", "user_uuid", expires_at.desc()),
    )
