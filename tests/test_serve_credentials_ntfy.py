"""Tests for the daemon POST/GET /api/v1/credentials/ntfy endpoints.

Closes extension's prop_kzpafwoykbae3lsikvuhxy5r4e. Mirror of the cortex
credentials write endpoint pair, with the critical constraint that
`topic` must be preserved on partial-update POSTs (cortex's channels
endpoint owns topic derivation; the extension never writes it).

Coverage:
1. POST with {url, token} writes both, preserves existing topic.
2. POST with token-only updates only token, leaves url + topic untouched.
3. POST with url-only updates only url, leaves token + topic untouched.
4. POST with neither returns ok=false with actionable error.
5. POST never returns full token over the wire — last-4 preview only.
6. POST atomic — failure mid-write leaves file intact (smoke-tested via
   loader behavior; the loader.save_ntfy_config has its own coverage).
7. GET returns url + topic + token-set flag + last-4 preview.
8. GET on missing/empty credentials returns ok=true with empty fields.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from empirica.api.serve_app import create_serve_app


def _mk_app_with_creds_dir(tmp_path: Path, monkeypatch):
    """Point credentials.yaml resolution at a tmp dir + return TestClient.

    Also redirects Path.home() to tmp_path so the loader's notify.yaml
    fallback can't pick up the host machine's real ntfy token via the
    `backends.ntfy.auth_env` indirection. CredentialsLoader is a
    singleton, so we reset its instance + cache so state from earlier
    tests can't leak.
    """
    from empirica.config.credentials_loader import CredentialsLoader

    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(tmp_path / "credentials.yaml"))
    monkeypatch.delenv("ORCHESTRATION_NTFY_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_TOKEN", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_USER", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_PASS", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    return TestClient(create_serve_app())


def _seed_creds(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "credentials.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ── POST writes both fields ────────────────────────────────────────────


def test_post_writes_url_and_token_preserves_topic(tmp_path, monkeypatch):
    _seed_creds(tmp_path, "ntfy:\n  topic: empirica-orchestration-events-david\n")
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)

    r = client.post(
        "/api/v1/credentials/ntfy",
        json={"url": "https://ntfy.example/", "token": "tk_abcdef1234567890"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["url"] == "https://ntfy.example"  # trailing slash stripped
    assert body["topic"] == "empirica-orchestration-events-david"
    assert body["token_set"] is True
    assert body["token_preview"] == "...7890"  # noqa: S105
    assert body["written_path"].endswith("credentials.yaml")

    # Disk state: topic intact, url + token written
    on_disk = (tmp_path / "credentials.yaml").read_text()
    assert "topic: empirica-orchestration-events-david" in on_disk
    assert "https://ntfy.example" in on_disk
    assert "tk_abcdef1234567890" in on_disk


# ── POST partial updates ───────────────────────────────────────────────


def test_post_token_only_preserves_url_and_topic(tmp_path, monkeypatch):
    _seed_creds(
        tmp_path,
        "ntfy:\n"
        "  url: https://ntfy.existing\n"
        "  topic: empirica-orchestration-events-david\n"
        "  token: tk_old_keep_or_replace\n",
    )
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)

    r = client.post("/api/v1/credentials/ntfy", json={"token": "tk_NEW_5678"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["url"] == "https://ntfy.existing"
    assert body["topic"] == "empirica-orchestration-events-david"
    assert body["token_preview"] == "...5678"  # noqa: S105

    on_disk = (tmp_path / "credentials.yaml").read_text()
    assert "tk_NEW_5678" in on_disk
    assert "tk_old_keep_or_replace" not in on_disk


def test_post_url_only_preserves_token_and_topic(tmp_path, monkeypatch):
    _seed_creds(
        tmp_path,
        "ntfy:\n  url: https://ntfy.old\n  topic: empirica-orchestration-events-david\n  token: tk_keep_this_99XY\n",
    )
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)

    r = client.post("/api/v1/credentials/ntfy", json={"url": "https://ntfy.new"})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "https://ntfy.new"
    assert body["topic"] == "empirica-orchestration-events-david"
    assert body["token_preview"] == "...99XY"  # noqa: S105


# ── POST input validation ──────────────────────────────────────────────


def test_post_with_neither_returns_actionable_error(tmp_path, monkeypatch):
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)
    r = client.post("/api/v1/credentials/ntfy", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "url or token required" in body["error"]


def test_post_never_returns_full_token(tmp_path, monkeypatch):
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)
    secret = "tk_super_secret_FULL_value_NEVER_LEAK_xyz1"  # noqa: S105
    r = client.post("/api/v1/credentials/ntfy", json={"token": secret})
    body = r.json()
    assert secret not in r.text
    assert body["token_preview"] == "...xyz1"  # noqa: S105


# ── POST does NOT touch other top-level sections ───────────────────────


def test_post_does_not_clobber_cortex_block(tmp_path, monkeypatch):
    _seed_creds(
        tmp_path,
        "cortex:\n"
        "  url: https://cortex.example\n"
        "  api_key: ctx_real_key_dontTouch\n"
        "ntfy:\n"
        "  topic: empirica-orchestration-events-david\n",
    )
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)

    r = client.post(
        "/api/v1/credentials/ntfy",
        json={"url": "https://ntfy.new", "token": "tk_new1234"},
    )
    assert r.status_code == 200
    on_disk = (tmp_path / "credentials.yaml").read_text()
    assert "ctx_real_key_dontTouch" in on_disk
    assert "https://cortex.example" in on_disk


# ── GET ────────────────────────────────────────────────────────────────


def test_get_returns_url_topic_preview(tmp_path, monkeypatch):
    _seed_creds(
        tmp_path,
        "ntfy:\n"
        "  url: https://ntfy.example\n"
        "  topic: empirica-orchestration-events-david\n"
        "  token: tk_present_abcdEFGH\n",
    )
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)

    r = client.get("/api/v1/credentials/ntfy")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["url"] == "https://ntfy.example"
    assert body["topic"] == "empirica-orchestration-events-david"
    assert body["token_set"] is True
    assert body["token_preview"] == "...EFGH"  # noqa: S105
    assert "tk_present_abcdEFGH" not in r.text


def test_get_on_empty_credentials_returns_ok_with_empty_fields(tmp_path, monkeypatch):
    client = _mk_app_with_creds_dir(tmp_path, monkeypatch)
    r = client.get("/api/v1/credentials/ntfy")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["token_set"] is False
    assert body["token_preview"] is None
