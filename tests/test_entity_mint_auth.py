"""Tests for the hosted entity-mint service-token guard.

Covers the co-spec (empirica-mesh-support/docs/entity-mint-service-token-spec.md):
activation, fail-closed bind, 401-or-proceed, rotation set, body-verbatim, and
the emk_ token format. All cortex/CRM calls are mocked — runs on any host.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from empirica.api import entity_mint_auth as ema

# ── token-set config parsing ─────────────────────────────────────────


def test_load_valid_tokens_parses_comma_set(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, " emk_a , emk_b ,, emk_c ")
    assert ema.load_valid_tokens() == {"emk_a", "emk_b", "emk_c"}


def test_load_valid_tokens_empty_when_unset(monkeypatch):
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    assert ema.load_valid_tokens() == set()
    assert ema.is_guard_active() is False


def test_is_guard_active_true_when_configured(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_x")
    assert ema.is_guard_active() is True


# ── loopback classification + fail-closed bind ───────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "LOCALHOST", " 127.0.0.1 "])
def test_is_loopback_host_true(host):
    assert ema.is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "git.getempirica.com", ""])  # noqa: S104 — test fixture hosts
def test_is_loopback_host_false(host):
    assert ema.is_loopback_host(host) is False


def test_assert_bind_safe_loopback_ok_without_token(monkeypatch):
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    ema.assert_bind_safe("127.0.0.1")  # no raise — same-box, auth-free


def test_assert_bind_safe_nonloopback_without_token_raises(monkeypatch):
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    with pytest.raises(RuntimeError, match="never be exposed unauthenticated"):
        ema.assert_bind_safe("0.0.0.0")  # noqa: S104 — test fixture host


def test_assert_bind_safe_nonloopback_with_token_ok(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_ok")
    ema.assert_bind_safe("0.0.0.0")  # noqa: S104 — test fixture host; no raise, token present


# ── bearer extraction ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer emk_tok", "emk_tok"),
        ("bearer emk_tok", "emk_tok"),
        ("Bearer  emk_tok ", "emk_tok"),
        ("Basic abc", None),
        ("emk_tok", None),
        ("Bearer ", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_bearer(header, expected):
    assert ema._extract_bearer(header) == expected


def test_generate_mint_token_format():
    tok = ema.generate_mint_token()
    assert tok.startswith("emk_")
    assert len(tok) > 40  # emk_ + 32-byte urlsafe (~43 chars)
    assert ema.generate_mint_token() != ema.generate_mint_token()  # random


# ── endpoint guard behaviour (TestClient) ────────────────────────────

_MINT_OK = {"ok": True, "entity_id": "c-jane-acme", "created": True, "matched_by": "email"}


def _client():
    from empirica.api.serve_app import create_serve_app
    return TestClient(create_serve_app())


def _post(client, bearer=None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return client.post(
        "/api/v1/entities",
        json={"type": "contact", "name": "Jane", "email": "jane@acme.com"},
        headers=headers,
    )


def test_guard_inactive_allows_no_auth(monkeypatch):
    """Loopback / no token configured → mint proceeds unauthenticated (back-compat)."""
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    with patch("empirica.cli.command_handlers.entity_commands.mint_contact", return_value=_MINT_OK):
        r = _post(_client())
    assert r.status_code == 200
    assert r.json() == _MINT_OK


def test_guard_active_valid_token_proceeds(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_valid")
    with patch("empirica.cli.command_handlers.entity_commands.mint_contact", return_value=_MINT_OK):
        r = _post(_client(), bearer="emk_valid")
    assert r.status_code == 200
    assert r.json() == _MINT_OK  # body verbatim under auth


def test_guard_active_missing_token_401(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_valid")
    with patch("empirica.cli.command_handlers.entity_commands.mint_contact", return_value=_MINT_OK) as m:
        r = _post(_client())  # no Authorization header
    assert r.status_code == 401
    m.assert_not_called()  # guard rejects before the mint runs


def test_guard_active_invalid_token_401(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_valid")
    with patch("empirica.cli.command_handlers.entity_commands.mint_contact", return_value=_MINT_OK) as m:
        r = _post(_client(), bearer="emk_wrong")
    assert r.status_code == 401
    m.assert_not_called()


def test_guard_active_rotation_set_both_tokens_work(monkeypatch):
    """During a rotation overlap the set holds old+new; both validate."""
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_old,emk_new")
    with patch("empirica.cli.command_handlers.entity_commands.mint_contact", return_value=_MINT_OK):
        client = _client()
        assert _post(client, bearer="emk_old").status_code == 200
        assert _post(client, bearer="emk_new").status_code == 200


def test_guard_active_wrong_type_still_auths_first(monkeypatch):
    """Auth runs as a dependency before the handler; a bad bearer 401s even for
    an otherwise-invalid body (no information leak about the 422 path)."""
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_valid")
    client = _client()
    r = client.post(
        "/api/v1/entities",
        json={"type": "organization", "name": "Acme"},
        headers={"Authorization": "Bearer emk_wrong"},
    )
    assert r.status_code == 401
