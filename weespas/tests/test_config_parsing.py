"""Config CSV-parsing properties for the security-hardening pass.

Covers the env-driven CORS allow-list and the M-Pesa callback IP allow-list — both
parse a comma-separated env string into a clean collection, and both must default
to the inert behaviour (dev CORS list works; no IP check) so dev/sandbox are
unchanged until an operator sets the env var.
"""
from PE.weespas.core.config import Settings, settings

_REQ = {"database_url": "postgresql://x:y@localhost/z", "secret_key": "test-secret"}


def test_cors_default_is_local_dev_list():
    # The live default keeps every local Vite/CRA port working out of the box.
    origins = settings.cors_origins_list
    assert "http://localhost:5173" in origins
    assert "http://localhost:5174" in origins
    assert all(o.startswith("http") for o in origins)


def test_cors_origins_parsed_from_csv_and_trimmed():
    s = Settings(**_REQ, cors_origins="https://a.com, https://b.com ,, https://c.com")
    assert s.cors_origins_list == ["https://a.com", "https://b.com", "https://c.com"]


def test_cors_empty_yields_empty_list():
    s = Settings(**_REQ, cors_origins="")
    assert s.cors_origins_list == []


def test_callback_ip_allowlist_default_empty_means_no_check():
    # Empty set ⇒ the callback handler skips the IP gate (sandbox/dev unchanged).
    assert settings.mpesa_callback_allowed_ip_set == set()


def test_callback_ip_allowlist_parsed_from_csv_and_trimmed():
    s = Settings(**_REQ, mpesa_callback_allowed_ips=" 196.201.214.200, 196.201.214.206 ")
    assert s.mpesa_callback_allowed_ip_set == {"196.201.214.200", "196.201.214.206"}


def test_checkout_rate_limit_defaults_are_generous():
    # A real buyer taps a few times; only automation should hit the cap.
    assert settings.checkout_rate_max >= 3
    assert settings.checkout_rate_window_s >= 60
