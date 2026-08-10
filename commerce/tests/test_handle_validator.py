"""Handle validator unit tests (pure function — no DB, no HTTP).

Covers every arm of the validator's failure modes + every reserved word, so a future edit that
loosens a rule (accidentally allowing "admin" as a handle, or a leading hyphen, or an uppercase
letter that shadows a lowercased peer in the case-insensitive index) fails a specific named test
instead of a fuzzy "something changed."
"""
from __future__ import annotations

import pytest

from PE.commerce.services.shops import (
    HandleError,
    _RESERVED_HANDLES,
    normalize_and_validate_handle,
)


class TestNormalization:
    def test_lowercases_uppercase(self) -> None:
        assert normalize_and_validate_handle("MamaMboga") == "mamamboga"

    def test_lowercases_mixed(self) -> None:
        assert normalize_and_validate_handle("NyAmA-ChOmA") == "nyama-choma"

    def test_trims_surrounding_whitespace(self) -> None:
        assert normalize_and_validate_handle("  kitengela-butcher  ") == "kitengela-butcher"

    def test_accepts_all_lowercase(self) -> None:
        assert normalize_and_validate_handle("mama-mboga") == "mama-mboga"

    def test_accepts_digits_and_alnum_mix(self) -> None:
        assert normalize_and_validate_handle("shop123") == "shop123"
        assert normalize_and_validate_handle("kite-2-butcher") == "kite-2-butcher"

    def test_accepts_min_length(self) -> None:
        assert normalize_and_validate_handle("abc") == "abc"

    def test_accepts_max_length(self) -> None:
        # 30 chars — the ceiling per _HANDLE_MAX.
        handle = "a" + "b" * 28 + "c"
        assert len(handle) == 30
        assert normalize_and_validate_handle(handle) == handle


class TestRequired:
    def test_none_raises_required(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle(None)
        assert ei.value.status_code == 422
        assert ei.value.detail == "handle-required"

    def test_empty_string_raises_required(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("")
        assert ei.value.detail == "handle-required"

    def test_whitespace_only_raises_required(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("   ")
        assert ei.value.detail == "handle-required"


class TestLength:
    def test_too_short_after_trim(self) -> None:
        # 2 chars is below _HANDLE_MIN (3).
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("ab")
        assert ei.value.status_code == 422
        assert ei.value.detail == "handle-length"

    def test_one_char_after_trim(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("a")
        assert ei.value.detail == "handle-length"

    def test_too_long_after_trim(self) -> None:
        # 31 chars is one over _HANDLE_MAX (30).
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("a" * 31)
        assert ei.value.detail == "handle-length"


class TestSyntax:
    def test_leading_hyphen_rejected(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("-abc")
        assert ei.value.detail == "handle-syntax"

    def test_trailing_hyphen_rejected(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("abc-")
        assert ei.value.detail == "handle-syntax"

    def test_double_hyphen_rejected(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("mama--mboga")
        assert ei.value.detail == "handle-syntax"

    def test_underscore_rejected(self) -> None:
        # Underscore is a common typo but not part of the kebab-case grammar.
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("mama_mboga")
        assert ei.value.detail == "handle-syntax"

    def test_space_inside_rejected(self) -> None:
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("mama mboga")
        assert ei.value.detail == "handle-syntax"

    def test_special_chars_rejected(self) -> None:
        for bad in ("mama.mboga", "mama+mboga", "mama/mboga", "mama@mboga", "mama'mboga"):
            with pytest.raises(HandleError) as ei:
                normalize_and_validate_handle(bad)
            assert ei.value.detail == "handle-syntax", f"expected syntax fail on {bad!r}"

    def test_unicode_rejected(self) -> None:
        # No non-ASCII path — a handle rides in a URL and must round-trip cleanly.
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("café-shop")
        assert ei.value.detail == "handle-syntax"

    def test_single_hyphen_between_alnum_ok(self) -> None:
        # Sanity: the grammar DOES allow single internal hyphens.
        assert normalize_and_validate_handle("a-b") == "a-b"
        assert normalize_and_validate_handle("a-b-c") == "a-b-c"


class TestReserved:
    @pytest.mark.parametrize("word", sorted(_RESERVED_HANDLES))
    def test_every_reserved_word_rejected(self, word: str) -> None:
        # Every entry in the deny-list must be rejected. If someone deletes an entry (say, moves
        # "admin" out of the URL space), this parametrized test's expectation vanishes for that
        # entry — the test list updates automatically from the source.
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle(word)
        assert ei.value.status_code == 422
        assert ei.value.detail == "handle-reserved", (
            f"reserved word {word!r} should be reserved-rejected, got {ei.value.detail!r}"
        )

    def test_reserved_check_is_case_insensitive(self) -> None:
        # Uppercase spelling of a reserved word must still be caught (lowercased first).
        with pytest.raises(HandleError) as ei:
            normalize_and_validate_handle("ADMIN")
        assert ei.value.detail == "handle-reserved"

    def test_a_close_but_non_reserved_word_is_accepted(self) -> None:
        # "help" is reserved; "helping" is not — sanity that the check is EQUALITY, not substring.
        assert normalize_and_validate_handle("helping") == "helping"
        # "shop" is reserved; "myshop" is not.
        assert normalize_and_validate_handle("myshop") == "myshop"
