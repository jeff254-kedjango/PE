"""
Unit tests for the GACOS submission-rejection heuristic
(scripts/fetch_gacos._looks_like_bounce).

Pure, fast, always-run — no network.

Regression guard for 2026-06-12: the old heuristic keyed on the
`googletagmanager` marker as a "rejected" tell, but that marker is present on
the SUCCESS response too. It therefore flagged a batch "REJECTED, no email will
arrive" when the emails in fact arrived and ingested cleanly — a confident false
alarm that sent a whole session down the wrong path.

The contract now:
  - a clean 200 page (even one carrying the GTM loader) is NOT a bounce;
  - only an explicit error marker in the body counts as a bounce;
  - so the function must never produce a false positive on success-shaped HTML.
"""

from __future__ import annotations

from scripts.fetch_gacos import _looks_like_bounce


# A success/landing page carries the Google Tag Manager loader — the exact thing
# the old code mistook for a rejection. This MUST NOT be flagged as a bounce.
_SUCCESS_PAGE = """
<!DOCTYPE html><html><head>
<script>(function(w,d,s,l,i){})(window,document,'script','dataLayer','GTM-XX''';
// googletagmanager.com/gtm.js
</head><body>Your request has been received.</body></html>
"""


def test_success_page_with_gtm_is_not_a_bounce():
    # The 2026-06-12 false-positive case: clean page, GTM present, job accepted.
    assert _looks_like_bounce(_SUCCESS_PAGE) is False


def test_empty_body_is_not_a_bounce():
    assert _looks_like_bounce("") is False


def test_explicit_error_markers_are_bounces():
    for body in (
        "Invalid date format supplied",
        "ERROR: bbox out of range",
        "You exceed the maximum number of dates",
        "too many dates in one submission",
        "the coordinates are not valid",
    ):
        assert _looks_like_bounce(body) is True, body


def test_matching_is_case_insensitive():
    assert _looks_like_bounce("INVALID DATE") is True
