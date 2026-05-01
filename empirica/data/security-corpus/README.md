# Bundled security corpus

This directory ships with the Empirica package and is copied to
`~/.empirica/security-corpus/` on `setup-claude-code` so users can edit
the canon for their own context. The Phase 2 services-auditor agent
cites these files when emitting findings; Phase 1 only ships them
(no consumer yet).

## Contents

| File | Source | Purpose |
|---|---|---|
| `owasp-llm-top10.md` | OWASP 2025 | LLM-specific vulnerability patterns |
| `owasp-agentic-top10.md` | OWASP Dec 2025 | Autonomous agent risk patterns |
| `nist-ai-rmf.md` | NIST AI RMF 1.0 | Governance + risk frame |
| `mitre-atlas.md` | MITRE ATLAS | Adversarial threat patterns against AI systems |
| `google-saif.md` | Google SAIF | Secure AI framework principles |

## Refresh policy

Phase 1 ships **stubs** — title, canonical URL, and section-header
skeleton. The full corpus content fills in via:

- the **Phase 3 corpus-refresh loop** (monthly cron) which pulls the
  latest published versions, OR
- a **manual commit** by anyone with the canonical text on hand.

Stubs are intentionally light so the package stays slim and the
auditor agent's citations point at structure that will hold up once
real content lands.
