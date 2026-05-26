# Security Audit Architecture

`empirica security-audit` is the AI-aware supply-chain and credential-rotation audit. It runs `pip-audit` against the active environment, cross-references findings with the CISA KEV catalogue, and emits a prioritised report scoped to what empirica itself ships versus what the user has installed alongside it.

This document describes the Phase 1 design (shipped) and the deferred phases on the roadmap.

---

## Why a separate command (vs the compliance pipeline)

The compliance pipeline already has a `dep_audit` check that runs `pip-audit` at release-tier. That check answers a binary question: *does this release ship known-vulnerable dependencies?*

`security-audit` answers a richer question: *which dependencies should we rotate first, and which are the user's problem, not ours?*

| | `compliance-report --tier release` (`dep_audit`) | `empirica security-audit` |
|---|---|---|
| Trigger | Pre-release gate | On-demand, user-driven |
| Output shape | pass/fail with violation count | Prioritised rotation report |
| Scope split | None — every finding counts | empirica-managed vs user-installed |
| External feed | pip-audit's own DB | pip-audit + CISA KEV catalogue (cached daily) |
| Failure semantics | Blocks release | `--passed=False` only if KEV-matched empirica-scoped finding |

Keeping them separate lets the compliance check stay fast and deterministic, while `security-audit` can grow into a multi-feed cross-ecosystem tool without bloating the release gate.

---

## Phase 1 — Shipped (1.9.x)

**Code surface:**

- `empirica/cli/command_handlers/security_audit_commands.py` — CLI entry, text/JSON formatter
- `empirica/core/security/audit.py` — `run_security_audit` orchestrator
- `empirica/core/security/kev_feed.py` — `KEVFeed` cache (CISA Known Exploited Vulnerabilities, JSON, refreshed daily)
- `empirica/core/security/scope.py` — `get_empirica_managed_packages` (empirica + its transitive `Requires` set)

**Pipeline:**

```
pip-audit (CVE/GHSA findings)
   │
   ▼
KEVFeed.match(cve_id)    ── KEV catalogue cached at ~/.empirica/kev_cache.json
   │
   ▼
scope.classify(package)  ── "empirica" if in empirica's transitive Requires,
                             "user" if installed but outside empirica's surface
   │
   ▼
report.rotate_priority   ── now (in KEV) | month (CVE only) | monitor | safe
```

**Rotation priority rubric:**

| Priority | Trigger |
|---|---|
| **now** | Finding matches a CVE on the CISA KEV catalogue (actively exploited in the wild) |
| **month** | CVE/GHSA finding, no KEV match — patch within the month |
| **monitor** | Yanked package / soft advisory — keep an eye on it |
| **safe** | No matching advisory after cross-reference |

**Pass/fail gate (CLI exit code):**

The audit returns `passed=False` (exit 1) ONLY if an **empirica-scoped finding matches KEV**. User-scoped KEV matches are surfaced as informational — empirica can't unilaterally rotate a package the user installed for their own reasons.

**CLI:**

```bash
empirica security-audit                          # text report
empirica security-audit --output json            # machine-readable
empirica security-audit --refresh-feeds          # force-refresh KEV cache
empirica security-audit --project-root <path>    # audit a different project
```

**Defaults:**

- pip-audit timeout: 180 seconds
- KEV cache: `~/.empirica/kev_cache.json`, refreshed if >24h old (or `--refresh-feeds`)
- Empirica-managed scope: `empirica` + `pip show empirica` `Requires` field, transitively

---

## Phase 2 — OSV direct, multi-ecosystem (deferred)

pip-audit covers Python only. Many empirica adjacent surfaces touch Node (extension), Rust (some compilers / vendored tools), and shell (release pipeline, doctor scripts).

**Plan:**

- Add OSV.dev as a direct backend alongside pip-audit (OSV unifies CVE/GHSA across PyPI/npm/cargo/maven/etc.)
- Per-ecosystem scope detectors (`scope.py` becomes pluggable)
- Same rotation rubric, same KEV cross-reference

**Why not yet:** scope is hard. The current Python-only path covers the bulk of empirica's actual attack surface. Multi-ecosystem becomes interesting when we ship Node sidecars or vendor Rust crates.

---

## Phase 3 — Local credential enumeration (deferred)

Spots in the user's environment where credentials sit unencrypted. Read-only enumeration only — never moves or mutates.

**Plan:**

- Walk `~/.config/{gh,gcloud,docker,...}`, `~/.empirica/credentials.yaml`, MCP server configs, common API-key env files
- Output a **rotation candidacy** report (last-modified age, scope of access, rotation recommendation)
- Cross-reference with public breach feeds (HIBP) when an account name maps cleanly

**Why not yet:** false-positive cost is high and the UX for surfacing "these creds look stale" needs more thought.

---

## Phase 4 — deps.dev OpenSSF Scorecards (deferred)

For top-risk dependencies, pull the OpenSSF Scorecard via deps.dev to surface maintenance signals (maintained, code-review, signed-releases, binary-artifacts).

**Plan:**

- After Phase 2's multi-ecosystem support, score the top-risk dependencies (by KEV match or pin-staleness)
- Render scorecard summary inline with the rotation report
- "Dependencies you should consider replacing" recommendation tier

---

## Regulatory mapping

The Phase 1 audit aligns with:

| Framework | Article / Clause | Requirement |
|---|---|---|
| **EU AI Act** | Art. 15(4) | Accuracy, robustness, cybersecurity — known-vulnerability monitoring |
| **ISO/IEC 42001** | 8.4 | AI system development — vulnerability management |
| **GDPR** | Art. 32 | Security of processing — appropriate technical measures |

The KEV cross-reference is what elevates this above a generic `pip-audit` run — it converts a flat finding list into an attack-likelihood prioritised report. KEV inclusion means a vulnerability is being actively exploited *now*, which is the signal compliance auditors actually care about.

---

## Validation precedent

The decision to include credential-rotation tracking (Phase 3) was sharpened by the April 2026 Vercel / Context.ai OAuth breach, where stale OAuth tokens granted lateral access to several downstream services. Empirica logs a `decision_log` for that incident; the relevant artifact ids are findable via `empirica project-search --task "vercel oauth"`.

---

## Source

- Catalog entry: `empirica/cli/parsers/monitor_parsers.py` (`security-audit` subparser)
- Handler: `empirica/cli/command_handlers/security_audit_commands.py`
- Core: `empirica/core/security/{audit,kev_feed,scope}.py`
- Compliance peer: `empirica/cli/command_handlers/compliance_report_commands.py` (`dep_audit` check)
