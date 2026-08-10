"""Auth primitives: password hashing and JWT round-trip.

Uses the real helpers in services.auth_service so a regression in token
signing or password verification is caught. No database involved.
"""

import pytest
from jose import jwt

from PE.weespas.core.config import settings
from PE.weespas.services.auth_service import (
    create_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret-pw")
    assert hashed != "s3cret-pw"          # never stored in plaintext
    assert verify_password("s3cret-pw", hashed) is True
    assert verify_password("wrong-pw", hashed) is False


def test_password_hashes_are_salted():
    # bcrypt salts per-call, so the same input yields different digests.
    assert hash_password("same") != hash_password("same")


def test_access_token_encodes_sub_and_role():
    token = create_access_token("user-123", role="agent")
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    assert payload["sub"] == "user-123"
    assert payload["role"] == "agent"
    assert "exp" in payload


def test_token_rejected_under_wrong_secret():
    token = create_access_token("user-123")
    with pytest.raises(Exception):
        jwt.decode(token, "a-different-secret", algorithms=[settings.algorithm])
