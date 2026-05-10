# Developer Documentation

**Technical guides for integrating AI assistants with Empirica.**

---

## Quick Start

| You Want To | Start Here |
|-------------|------------|
| Set up Claude Code | [CLAUDE_CODE_SETUP.md](CLAUDE_CODE_SETUP.md) |
| Read the live system prompt | `~/.claude/empirica-system-prompt.md` (after `empirica setup-claude-code`) |
| Learn the CLI | [CLI_COMMANDS_UNIFIED.md](CLI_COMMANDS_UNIFIED.md) |
| Build custom skills | [skills/](skills/) |

---

## AI Integration

| Guide | Purpose |
|-------|---------|
| [CLAUDE_CODE_SETUP.md](CLAUDE_CODE_SETUP.md) | Claude Code hooks and integration |
| [AI_SELF_MANAGEMENT.md](AI_SELF_MANAGEMENT.md) | AI self-management patterns |
| [MULTI_SESSION_LEARNING.md](MULTI_SESSION_LEARNING.md) | Cross-session knowledge persistence |

---

## System Prompts

The live system prompt is generated from a single lean source and
deployed via `empirica setup-claude-code`:

```
empirica/plugins/claude-code-integration/templates/
├── empirica-system-prompt-lean.md   # Canonical lean source (default)
└── CLAUDE.md                        # Legacy verbose template (--full-prompt opt-in)
```

After `empirica setup-claude-code`:

- `~/.claude/empirica-system-prompt.md` — lean prompt (~263 lines), overwritten on each setup
- `~/.claude/CLAUDE.md` — `@include` reference to the above; preserves any user overrides

The previous `CANONICAL_CORE.md + model_deltas/` pipeline was retired
in 1.7. See [AI_SELF_MANAGEMENT.md](AI_SELF_MANAGEMENT.md) for the
current source-of-truth paths.

---

## Reference

| Guide | Purpose |
|-------|---------|
| [CLI_COMMANDS_UNIFIED.md](CLI_COMMANDS_UNIFIED.md) | Complete CLI reference |
| [MCP_SERVER_REFERENCE.md](MCP_SERVER_REFERENCE.md) | MCP server API |
| [EPISTEMIC_HEALTH_QUICK_REFERENCE.md](EPISTEMIC_HEALTH_QUICK_REFERENCE.md) | Vector quick reference |
| [doppler_secrets_guide_for_ais.md](doppler_secrets_guide_for_ais.md) | Secrets management |

---

## Skills Development

| Guide | Purpose |
|-------|---------|
| [skills/](skills/) | Skill pipeline development |

---

## Security

| Guide | Purpose |
|-------|---------|
| [Security/](Security/) | Security guidelines and privacy agents |

---

## Integrations

| Guide | Purpose |
|-------|---------|
| [BEADS_INTEGRATION_DESIGN.md](BEADS_INTEGRATION_DESIGN.md) | BEADS technical design |
| [BEADS_GIT_BRIDGE.md](BEADS_GIT_BRIDGE.md) | Git bridge setup |

---

## Key Concepts

### CASCADE Workflow
```
PREFLIGHT → CHECK → POSTFLIGHT → POST-TEST
```

POST-TEST automatically collects objective evidence (tests, git, goals) to ground
self-assessments in reality. See dual-track calibration in CLAUDE.md.

### 13 Epistemic Vectors
- **Foundation:** engagement, know, do, context
- **Comprehension:** clarity, coherence, signal, density
- **Execution:** state, change, completion, impact
- **Meta:** uncertainty

### Readiness Gate
Sentinel computes thresholds dynamically from calibration data in `.breadcrumbs.yaml`.

---

**For architecture details:** See [../../architecture/](../../architecture/)
