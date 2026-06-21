"""
Domain registry CLI command handlers (A1 — SPEC 1 Part 1).

Commands:
  domain-list       List all loaded domains
  domain-show       Show a domain's checklist details
  domain-resolve    Resolve a (work_type, domain, criticality) tuple
  domain-validate   Validate all YAML domain files
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from empirica.config.domain_registry import DomainKey, DomainRegistry


def _get_registry(args) -> DomainRegistry:
    """Build registry from project context."""
    project_path = getattr(args, "project_path", None)
    if project_path is None:
        try:
            from empirica.utils.session_resolver import InstanceResolver

            project_path = InstanceResolver.project_path()
        except Exception:
            pass
    return DomainRegistry(
        project_path=Path(project_path) if project_path else None,
    )


def handle_domain_list_command(args):
    """List all loaded domains."""
    reg = _get_registry(args)
    domains = reg.list_domains()
    output = getattr(args, "output", "text")

    if output == "json":
        result: dict[str, Any] = {"ok": True, "domains": []}
        for name in domains:
            entry = reg.get_domain_entry(name)
            result["domains"].append(
                {
                    "name": name,
                    "description": entry.description if entry else "",
                    "criticalities": reg.list_criticalities(name),
                    "source": entry.source if entry else "unknown",
                }
            )
        print(json.dumps(result, indent=2))
    else:
        print(f"Loaded domains ({len(domains)}):\n")
        for name in domains:
            entry = reg.get_domain_entry(name)
            desc = entry.description[:60] if entry else ""
            crits = ", ".join(reg.list_criticalities(name))
            source = f"[{entry.source}]" if entry else ""
            print(f"  {name:20s} {source:10s} ({crits})")
            if desc:
                print(f"  {'':20s}            {desc}")

    return {"ok": True}


def handle_domain_show_command(args):
    """Show a domain's checklist details."""
    reg = _get_registry(args)
    domain_name = args.domain
    entry = reg.get_domain_entry(domain_name)
    output = getattr(args, "output", "text")

    if entry is None:
        msg = f"Domain '{domain_name}' not found. Use 'domain-list' to see available domains."
        if output == "json":
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"❌ {msg}")
        return {"ok": False, "error": msg}

    if output == "json":
        result = {
            "ok": True,
            "domain": domain_name,
            "description": entry.description,
            "applies_to_work_types": entry.applies_to_work_types,
            "source": entry.source,
            "criticalities": {},
        }
        for level, cl in sorted(entry.criticalities.items()):
            result["criticalities"][level] = {
                "required_checks": list(cl.required),
                "optional_checks": list(cl.optional),
                "thresholds": cl.thresholds,
                "max_iterations": cl.max_iterations,
                "hints_to_ai": list(cl.hints_to_ai),
            }
        if entry.hints_to_ai:
            result["hints_to_ai"] = list(entry.hints_to_ai)
        print(json.dumps(result, indent=2))
    else:
        print(f"Domain: {domain_name}")
        print(f"Description: {entry.description}")
        print(f"Source: {entry.source}")
        if entry.applies_to_work_types:
            print(f"Applies to: {', '.join(entry.applies_to_work_types)}")
        else:
            print("Applies to: all work types")
        print()

        for level in ("low", "medium", "high", "critical"):
            cl = entry.criticalities.get(level)
            if cl is None:
                continue
            print(f"  {level}:")
            if cl.required:
                print(f"    Required: {', '.join(cl.required)}")
            else:
                print("    Required: (none — self-assessment stands)")
            if cl.optional:
                print(f"    Optional: {', '.join(cl.optional)}")
            if cl.thresholds:
                thresh = ", ".join(f"{k}={v}" for k, v in cl.thresholds.items())
                print(f"    Thresholds: {thresh}")
            print(f"    Max iterations: {cl.max_iterations}")
            if cl.hints_to_ai:
                for hint in cl.hints_to_ai:
                    print(f"    Hint: {hint}")
            print()

    return {"ok": True}


def handle_domain_resolve_command(args):
    """Resolve a (work_type, domain, criticality) tuple to its checklist."""
    reg = _get_registry(args)
    key = DomainKey(
        work_type=args.work_type,
        domain=args.domain or "default",
        criticality=args.criticality or "medium",
    )
    cl = reg.resolve(key)
    output = getattr(args, "output", "text")

    if output == "json":
        print(
            json.dumps(
                {
                    "ok": True,
                    "key": {"work_type": key.work_type, "domain": key.domain, "criticality": key.criticality},
                    "checklist": {
                        "required_checks": list(cl.required),
                        "optional_checks": list(cl.optional),
                        "has_checks": cl.has_checks,
                        "thresholds": cl.thresholds,
                        "max_iterations": cl.max_iterations,
                        "hints_to_ai": list(cl.hints_to_ai),
                    },
                },
                indent=2,
            )
        )
    else:
        print(f"Resolve: ({key.work_type}, {key.domain}, {key.criticality})")
        print()
        if cl.has_checks:
            print(f"  Required: {', '.join(cl.required)}")
            if cl.optional:
                print(f"  Optional: {', '.join(cl.optional)}")
            if cl.thresholds:
                thresh = ", ".join(f"{k}={v}" for k, v in cl.thresholds.items())
                print(f"  Thresholds: {thresh}")
            print(f"  Max iterations: {cl.max_iterations}")
        else:
            print("  No checks required — self-assessment stands.")

    return {"ok": True}


def handle_domain_validate_command(args):
    """Validate all YAML domain files."""
    reg = _get_registry(args)
    domains = reg.list_domains()
    output = getattr(args, "output", "text")
    errors = []

    for name in domains:
        entry = reg.get_domain_entry(name)
        if entry is None:
            errors.append(f"{name}: entry is None after loading")
            continue
        if not entry.criticalities:
            errors.append(f"{name}: no criticality levels defined")

    if output == "json":
        print(
            json.dumps(
                {
                    "ok": len(errors) == 0,
                    "domains_validated": len(domains),
                    "errors": errors,
                },
                indent=2,
            )
        )
    else:
        if errors:
            print(f"❌ Validation found {len(errors)} error(s):")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"✓ All {len(domains)} domain(s) valid.")

    return {"ok": len(errors) == 0}
