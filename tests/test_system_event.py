"""Tests for system_event — the diagnostics → cortex /v1/system/event core."""

from __future__ import annotations

import io
import json

from empirica.cli.command_handlers import system_event as se


def _checks(*statuses):
    return [{"check": f"c{i}", "status": s} for i, s in enumerate(statuses)]


# ── _overall_from_checks ────────────────────────────────────────────────


def test_overall_all_pass():
    assert se._overall_from_checks(_checks("pass", "pass")) == ("pass", 2, 2)


def test_overall_any_fail_is_fail():
    assert se._overall_from_checks(_checks("pass", "fail", "pass")) == ("fail", 2, 3)


def test_overall_soft_only_is_warn():
    # unavailable/no_data are skips, not failures → warn (not fail)
    assert se._overall_from_checks(_checks("pass", "unavailable", "no_data")) == ("warn", 1, 1)


def test_overall_falls_back_to_passed_bool_on_unknown_status():
    overall, passed, total = se._overall_from_checks(
        [{"check": "x", "status": "weird", "passed": True},
         {"check": "y", "status": "weird", "passed": False}]
    )
    assert overall == "fail" and passed == 1 and total == 2


# ── compliance_report_to_event ──────────────────────────────────────────


def test_event_envelope_shape_pass():
    report = {"score": 100, "checks": _checks("pass", "pass")}
    ev = se.compliance_report_to_event(
        report, ran_by="empirica.david.empirica", ran_at="2026-06-16T00:00:00Z",
        suite="empirica-compliance", suite_version="1.0",
    )
    assert ev["category"] == "diagnostics"
    assert ev["event_type"] == "diagnostics_pass"
    assert ev["severity"] == "info"
    assert ev["deduplicate_key"] == "diagnostics:empirica.david.empirica:empirica-compliance"
    assert "2/2 checks pass" in ev["summary"] and "score 100" in ev["summary"]
    assert ev["details"]["overall"] == "pass"
    assert ev["details"]["checks"] == report["checks"]
    assert "org_id" not in ev  # omitted when None


def test_event_envelope_fail_is_critical():
    ev = se.compliance_report_to_event(
        {"checks": _checks("fail")}, ran_by="x", ran_at="t",
    )
    assert ev["event_type"] == "diagnostics_fail"
    assert ev["severity"] == "critical"


def test_event_envelope_includes_org_id_when_given():
    ev = se.compliance_report_to_event(
        {"checks": _checks("unavailable")}, ran_by="x", ran_at="t", org_id="org-nle",
    )
    assert ev["org_id"] == "org-nle"
    assert ev["event_type"] == "diagnostics_warn"  # soft-only


# ── emit_system_event ───────────────────────────────────────────────────


def test_emit_posts_to_system_event_endpoint(monkeypatch):
    captured = {}

    class _Resp(io.BytesIO):
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data.decode())
        return _Resp(json.dumps({"ok": True, "event_id": "ev_1"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    status, body = se.emit_system_event(
        {"category": "diagnostics"}, cortex_url="https://cortex.example", api_key="ctx_k",
    )
    assert status == 200 and body["event_id"] == "ev_1"
    assert captured["url"] == "https://cortex.example/v1/system/event"
    assert captured["auth"] == "Bearer ctx_k"


def test_emit_returns_error_without_config(monkeypatch):
    monkeypatch.setattr(se, "resolve_cortex_config", lambda: (None, None))
    status, body = se.emit_system_event({"category": "diagnostics"})
    assert status == -1 and "error" in body
