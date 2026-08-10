"""Payment-rail seam — the boundary between our LEDGER and a real money rail.

⚠ DELIBERATELY A STUB THIS INCREMENT. The architecture doc (§6) forbids writing live M-Pesa
code before the Daraja party-direct / B2C / hold-window research is done ("Do not write
settlement on memory"). So settlement records the obligation in OUR database (atomic in the
ledger) and hands the *rail* step to this seam, whose only implementation is an inert stub.

When the research lands, a real ``DarajaRail`` implements the same Protocol and ``get_rail``
selects it via ``COMMERCE_PAYMENT_RAIL=daraja`` — purely additive, no settlement-logic change.
Until then ``get_rail`` RAISES if daraja is requested, so a misconfiguration can never silently
move (or pretend to move) real money.

The two rail events mirror the doc's party-direct model:
  * ``record_obligation`` — the buyer pays the SELLER 100% of the locked price directly (no
    float touches us); we just record that the obligation exists.
  * ``collect_commission`` — we collect our 3% on a SECOND rail.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from PE.commerce.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RailResult:
    """Outcome of a rail call. ``ok`` gates the settlement state transition; ``ref`` is the
    rail's reference (a real Daraja receipt later; a synthetic id from the stub now)."""
    ok: bool
    ref: str
    detail: str = ""


@runtime_checkable
class PaymentRail(Protocol):
    def record_obligation(self, *, order_id: str, locked_price_cents: int,
                          seller_id: str) -> RailResult: ...

    def collect_commission(self, *, order_id: str, commission_cents: int) -> RailResult: ...


class StubRail:
    """Inert rail: moves no money, returns a deterministic synthetic ref, logs a dry-run line.
    Deterministic (ref derived from order_id) so tests and the event hash-chain are stable."""

    def record_obligation(self, *, order_id: str, locked_price_cents: int,
                          seller_id: str) -> RailResult:
        logger.info("STUB rail record_obligation order=%s amount=%s seller=%s (no money moved)",
                    order_id, locked_price_cents, seller_id)
        return RailResult(ok=True, ref=f"stub-obl-{order_id}", detail="dry_run")

    def collect_commission(self, *, order_id: str, commission_cents: int) -> RailResult:
        logger.info("STUB rail collect_commission order=%s commission=%s (no money moved)",
                    order_id, commission_cents)
        return RailResult(ok=True, ref=f"stub-com-{order_id}", detail="dry_run")


_STUB = StubRail()


def get_rail() -> PaymentRail:
    """Return the configured rail. Only "stub" exists this increment. A request for the real
    "daraja" rail RAISES (it isn't implemented yet) rather than silently degrading — money code
    must fail loud, never no-op."""
    name = (settings.payment_rail or "stub").strip().lower()
    if name == "stub":
        return _STUB
    raise RuntimeError(
        f"payment rail '{name}' is not implemented yet. Only the inert 'stub' rail exists; the "
        "real Daraja rail is blocked on the §6 party-direct/B2C research. Set "
        "COMMERCE_PAYMENT_RAIL=stub (or leave it unset) until that ships."
    )
