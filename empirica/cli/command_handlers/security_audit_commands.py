"""CLI handler for `empirica security-audit`.

Phase 1: pip-audit + CISA KEV cross-reference. See
empirica/core/security/audit.py for the audit logic and
docs/architecture/SECURITY_AUDIT.md for the design.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def handle_security_audit_command(args) -> None:
    """Run security-audit and emit text or JSON report."""
    from empirica.core.security import run_security_audit

    project_root = Path(getattr(args, "project_root", ".") or ".").resolve()
    refresh = bool(getattr(args, "refresh_feeds", False))
    output_format = getattr(args, "output", "text")

    report = run_security_audit(project_root, refresh_feeds=refresh)

    if output_format == "json":
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["passed"] else 1)

    _print_text_report(report)
    sys.exit(0 if report["passed"] else 1)


def _print_text_report(report: dict[str, Any]) -> None:
    """Emit a human-readable security audit report.

    Findings are split into two scopes:
      EMPIRICA-MANAGED — fixes are empirica's responsibility (gates pass/fail)
      USER-INSTALLED   — outside empirica's surface (informational only)
    """
    summary = report.get("summary", {})
    scanned = report.get("scanned", {})
    kev_meta = report.get("kev_metadata", {})
    scope_meta = report.get("scope_metadata", {})

    status = "[PASS]" if report.get("passed") else "[FAIL]"
    print("=" * 60)
    print(f"EMPIRICA SECURITY AUDIT  {status}")
    print("=" * 60)
    print(f"  Tool:      {scanned.get('tool', '?')} ({scanned.get('status', '?')})")
    if scanned.get("dependencies_scanned") is not None:
        print(f"  Scanned:   {scanned['dependencies_scanned']} dependencies in active venv")
    if scope_meta:
        print(
            f"  Scope:     {scope_meta.get('empirica_managed_count', 0)} "
            f"empirica-managed packages "
            f"(empirica root {'present' if scope_meta.get('empirica_root_present') else 'NOT present'})"
        )
    if "error" in kev_meta:
        print(f"  KEV:       UNAVAILABLE — {kev_meta['error']}")
    else:
        age = kev_meta.get("cache_age_hours")
        age_str = f"cache {age:.1f}h old" if age is not None else "fresh"
        print(
            f"  KEV:       v{kev_meta.get('catalog_version', '?')} "
            f"({kev_meta.get('total_entries', '?')} entries, {age_str})"
        )
    print(f"  Findings:  {summary.get('total', 0)} total")
    emp = summary.get("empirica", {})
    usr = summary.get("user", {})
    print(
        f"             - empirica:      {emp.get('total', 0)} "
        f"({emp.get('now', 0)} now, {emp.get('month', 0)} month, "
        f"{emp.get('monitor', 0)} monitor, {emp.get('safe', 0)} safe)"
    )
    print(
        f"             - user-installed: {usr.get('total', 0)} "
        f"({usr.get('now', 0)} now, {usr.get('month', 0)} month, "
        f"{usr.get('monitor', 0)} monitor, {usr.get('safe', 0)} safe)"
    )
    print(f"  Duration:  {report.get('duration_seconds', 0)}s")
    print()

    findings = report.get("findings", [])
    if not findings:
        print("  No vulnerabilities found.")
    else:
        _print_scope_section(
            findings,
            scope="empirica",
            heading="EMPIRICA-MANAGED FINDINGS  (empirica's responsibility — gates pass/fail)",
        )
        _print_scope_section(
            findings,
            scope="user",
            heading="USER-INSTALLED FINDINGS  (outside empirica's surface — informational)",
        )

    print("=" * 60)
    fw = report.get("frameworks", {})
    if fw:
        print("  Regulatory mapping:")
        for k, v in fw.items():
            print(f"    -> {k}: {v}")
    print("=" * 60)


def _print_scope_section(findings: list[dict[str, Any]], *, scope: str, heading: str) -> None:
    """Render one scope's findings, grouped by priority."""
    bucket = [f for f in findings if f.get("scope") == scope]
    if not bucket:
        return
    print(f"  {heading}")
    print(f"  {'=' * len(heading)}")
    for priority, label in (
        ("now", "ROTATE NOW (in CISA KEV — actively exploited)"),
        ("month", "ROTATE THIS MONTH (CVE without observed exploitation)"),
        ("monitor", "MONITOR"),
        ("safe", "SAFE"),
    ):
        priority_bucket = [f for f in bucket if f.get("rotate_priority") == priority]
        if not priority_bucket:
            continue
        print(f"    {label}")
        print(f"    {'-' * len(label)}")
        for f in priority_bucket:
            _print_finding(f, indent="      ")
        print()


def _print_finding(f: dict[str, Any], indent: str = "    ") -> None:
    pkg = f"{f.get('package', '?')}=={f.get('version', '?')}"
    cves = ", ".join(f.get("cve_ids", [])) or f.get("vulnerability_id", "?")
    fix_str = ", ".join(f.get("fix_versions", [])) or "(no fix listed)"
    print(f"{indent}{pkg}")
    print(f"{indent}  CVE:  {cves}")
    print(f"{indent}  Fix:  {fix_str}")
    if f.get("kev_entry"):
        kev = f["kev_entry"]
        print(f"{indent}  KEV:  added {kev.get('date_added')}, due {kev.get('due_date')}")
        if kev.get("ransomware_campaign_use"):
            print(f"{indent}  RANSOMWARE: {kev['ransomware_campaign_use']}")
    desc = f.get("description", "")
    if desc:
        print(f"{indent}  Note: {desc[:120]}{'...' if len(desc) > 120 else ''}")
