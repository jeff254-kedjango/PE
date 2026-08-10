"""Veto-only text signals parsed from a listing's title/description.

The disambiguating resolver (services/insar_resolver.py) must pick the right building
when a dropped pin sits in a cluster of footprints. Listing TEXT is the weakest, most
adversarial signal: agents write marketing ("Luxury Penthouse", "Executive Tower"), so
text is used in exactly ONE direction — to VETO a candidate that physically cannot
contain the unit, never to PROMOTE a match toward a taller/safer building.

Why veto-only (the security rule, work_flow.md / session rule #3): if text could add
score, an agent could escape a CRITICAL footprint's risk tier just by writing a fancier
word, steering the auto-match onto the calm building next door. Eliminating impossible
candidates is always safe; pulling toward "nicer" ones is exactly the gaming vector we
refuse. So `FloorSignal` is consumed only by the resolver's veto step.

Parsing is deliberately conservative: a parse miss yields an empty signal (no veto), and
the resolver additionally NEVER vetoes its candidate set down to empty — an over-eager
description can therefore only ever push a listing toward `needs_confirmation` (a human
taps the right building), never toward `not_monitored` or a wrong auto-pick.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A real building won't exceed this; anything above is a typo / address number / hype.
# Caps the veto so "apartment 9999" can't eliminate every plausible candidate.
_MAX_PLAUSIBLE_FLOOR = 60

# Word-ordinals → floor number. "ground floor" is floor 1 (the lowest), so it never
# vetoes anything (every footprint has at least 1 floor); included for completeness.
_WORD_ORDINALS = {
    "ground": 1, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12,
}

# "5th floor", "3 storey", "10th flr", "floor 7" — capture the number either before or
# after the floor word. \b guards keep "12thfloorish" or part-words out.
_NUM_FLOOR_RE = re.compile(
    r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:floor|storey|story|flr)\b"
    r"|\b(?:floor|storey|story|flr)\s*(\d{1,2})\b",
    re.IGNORECASE,
)
_WORD_FLOOR_RE = re.compile(
    r"\b(" + "|".join(_WORD_ORDINALS) + r")\s*(?:floor|storey|story|flr)\b",
    re.IGNORECASE,
)
# Top-of-building cues. These imply "needs more than a single-storey building" only when
# a taller candidate exists; the resolver decides that, here we just flag the cue.
_PENTHOUSE_RE = re.compile(r"\b(penthouse|roof\s*top|rooftop|top\s+floor)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FloorSignal:
    """A parsed, veto-only floor hint.

    min_required_floors: the unit sits on at least this floor, so any candidate footprint
        with fewer floors than this CANNOT contain it (the veto). 0 ⇒ no constraint.
    penthouse: a top-of-building cue was present (a softer "prefer a tall building", but
        applied only as a veto-when-a-taller-alternative-exists, never as a score boost).
    """
    min_required_floors: int = 0
    penthouse: bool = False

    @property
    def has_constraint(self) -> bool:
        return self.min_required_floors > 0 or self.penthouse


_EMPTY = FloorSignal()


def parse_floor_signals(title: str | None, description: str | None) -> FloorSignal:
    """Extract a veto-only floor constraint from a listing's title + description.

    Pure + side-effect-free (unit-testable). Returns `_EMPTY` on no/garbage input. The
    highest floor number mentioned wins (a "5th-floor unit with a 2nd-floor balcony" still
    needs ≥5 floors). Absurd numbers (> _MAX_PLAUSIBLE_FLOOR) are ignored, not clamped, so
    a stray address number can't manufacture a veto.
    """
    text = " ".join(p for p in (title, description) if p).strip()
    if not text:
        return _EMPTY

    floors: list[int] = []
    for m in _NUM_FLOOR_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if raw is None:
            continue
        n = int(raw)
        if 1 <= n <= _MAX_PLAUSIBLE_FLOOR:
            floors.append(n)
    for m in _WORD_FLOOR_RE.finditer(text):
        floors.append(_WORD_ORDINALS[m.group(1).lower()])

    penthouse = bool(_PENTHOUSE_RE.search(text))
    min_required = max(floors) if floors else 0
    if min_required == 0 and not penthouse:
        return _EMPTY
    return FloorSignal(min_required_floors=min_required, penthouse=penthouse)
