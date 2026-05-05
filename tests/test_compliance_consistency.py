"""Regression test: JSON and human compliance-report formats must agree.

Background: a goal once flagged that the human-formatted header showed
[FAIL] 91% (10/11) while the JSON output of the same run reported
fully_compliant 11/11 score 1.0. Investigation found no separate
computation path — both formats read the same `report["overall"]` dict
produced by `_compute_overall_status`. The bug was not reproducible.

These tests lock in that invariant: status, score, and pass/fail counts
shown to humans must be identical to what JSON consumers see. Any future
refactor that introduces a separate human-side computation will fail
these tests immediately.
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout
from typing import Any

from empirica.cli.command_handlers.compliance_report_commands import (
    _compute_overall_status,
    _print_human_report,
)


def _human_output_for(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_human_report(report)
    return buf.getvalue()


def _make_report(
    pass_count: int = 11, fail_count: int = 0, unavailable_count: int = 0
) -> dict[str, Any]:
    """Build a synthetic compliance report dict with the requested mix."""
    results: list[dict[str, Any]] = []
    for i in range(pass_count):
        results.append({"check": f"check_{i}", "passed": True, "status": "pass"})
    for i in range(fail_count):
        results.append({"check": f"fail_{i}", "passed": False, "status": "fail"})
    for i in range(unavailable_count):
        results.append({"check": f"na_{i}", "passed": None, "status": "unavailable"})

    overall = _compute_overall_status(results)
    return {
        "report_version": "1.0",
        "timestamp": "2026-05-05T00:00:00Z",
        "project_root": "/test",
        "overall": overall,
        "checks": results,
        "regulatory_frameworks": [],
    }


# ── Status icon ↔ JSON status agreement ──────────────────────────────────


def test_human_pass_icon_when_json_says_fully_compliant():
    report = _make_report(pass_count=11, fail_count=0)
    assert report["overall"]["status"] == "fully_compliant"
    out = _human_output_for(report)
    assert "[PASS]" in out
    assert "[FAIL]" not in out


def test_human_fail_icon_when_json_says_non_compliant():
    report = _make_report(pass_count=10, fail_count=1)
    assert report["overall"]["status"] == "non_compliant"
    out = _human_output_for(report)
    assert "[FAIL]" in out
    assert "[PASS]" not in out


def test_human_partial_icon_when_json_says_compliant_with_gaps():
    report = _make_report(pass_count=10, fail_count=0, unavailable_count=1)
    assert report["overall"]["status"] == "compliant_with_gaps"
    out = _human_output_for(report)
    assert "[PARTIAL]" in out


# ── Score ↔ JSON score agreement ─────────────────────────────────────────


def test_human_score_matches_json_score_full():
    report = _make_report(pass_count=11, fail_count=0)
    out = _human_output_for(report)
    assert report["overall"]["score"] == 1.0
    # Header shows 100% (11/11)
    assert "100%" in out
    assert "(11/11)" in out


def test_human_score_matches_json_score_partial():
    """The bug-report shape: 10/11 passed → 91% in both formats."""
    report = _make_report(pass_count=10, fail_count=1)
    out = _human_output_for(report)
    assert report["overall"]["score"] == round(10 / 11, 4)
    # Human shows 91% (rounding) and (10/11)
    match = re.search(r"Score:\s+(\d+)%\s+\((\d+)/(\d+)\)", out)
    assert match is not None
    pct, passed, total = int(match.group(1)), int(match.group(2)), int(match.group(3))
    assert passed == report["overall"]["checks_passed"] == 10
    assert total == report["overall"]["checks_total"] == 11
    # JSON score 0.9091 → human 91% (banker's-style 0.5+ rounds up)
    assert pct == round(report["overall"]["score"] * 100)


def test_human_score_zero_when_all_fail():
    report = _make_report(pass_count=0, fail_count=5)
    out = _human_output_for(report)
    assert report["overall"]["score"] == 0.0
    assert "0%" in out
    assert "(0/5)" in out


# ── Single-source-of-truth invariant ─────────────────────────────────────


def test_status_icon_only_derives_from_overall_status():
    """Defensive: human output must read status from `overall["status"]`,
    not recompute from `checks`. We patch overall.status to a sentinel and
    assert the icon follows it (proves no parallel computation)."""
    report = _make_report(pass_count=10, fail_count=1)
    # Override the JSON-reported status to fully_compliant (mismatched with
    # the actual fail in checks). If the human formatter recomputed from
    # checks, it would still print [FAIL]. If it correctly reads from
    # overall.status, it'll print [PASS] — proving single source of truth.
    report["overall"]["status"] = "fully_compliant"
    out = _human_output_for(report)
    assert "[PASS]" in out
    assert "[FAIL]" not in out


def test_score_only_derives_from_overall_score():
    """Same defensive invariant for score: human reads overall.score, not
    the per-check counts."""
    report = _make_report(pass_count=11, fail_count=0)
    # Override to a sentinel value that wouldn't be derivable from 11/11
    report["overall"]["score"] = 0.42
    out = _human_output_for(report)
    assert "42%" in out


# ── Property: any synthetic state yields agreement ─────────────────────


def test_invariant_holds_across_pass_count_range():
    """For any pass/fail mix, the human output's percentage and (passed/total)
    counts must match the JSON overall block. Sweeps the parameter space
    so future refactors that diverge the two paths fail loudly."""
    for pass_count in (0, 1, 5, 10, 11):
        for fail_count in (0, 1, 3):
            report = _make_report(pass_count=pass_count, fail_count=fail_count)
            overall = report["overall"]
            out = _human_output_for(report)
            # Score percentage shown in human output matches JSON score
            expected_pct = round(overall["score"] * 100)
            assert f"{expected_pct}%" in out, (
                f"pct mismatch for pass={pass_count} fail={fail_count}: "
                f"expected {expected_pct}% in human output"
            )
            # Counts shown match
            expected_counts = f"({overall['checks_passed']}/{overall['checks_total']})"
            assert expected_counts in out, (
                f"counts mismatch for pass={pass_count} fail={fail_count}: "
                f"expected {expected_counts}"
            )
