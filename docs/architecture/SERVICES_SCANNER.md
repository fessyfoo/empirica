# AI Service Scanner — Phase 1 (Deterministic Inventory)

**Status:** Phase 1 shipped (this document graduates `PROPOSAL_AI_SERVICE_SCANNER.md`).
**Phases:**
- **1 (this doc):** deterministic data collection, read-surface YAML, Markdown report, bundled corpus stubs
- **2 (next):** AI judgment via the `services-auditor` persona — uses the bundled corpus and emits findings with confidence + cited sections
- **3:** biweekly loop + ntfy + history/diff verbs + corpus-refresh
- **4:** RAG over corpus, dynamic CVE feed, fleet view

---

## What Phase 1 ships

`empirica scan` runs a one-shot deterministic snapshot of the running
state of an AI-touching dev machine and emits Markdown (default) or
JSON. No persistent daemon, no AI judgment, no actions taken.

```bash
empirica scan                        # Markdown report
empirica scan --output json          # JSON envelope, suitable for piping
empirica scan --save                 # Persist to ~/.empirica/scans/<scan_id>.json
```

The scanner's own process appears in its output, tagged with
`is_scanner_self: true`, so you always see what the scanner cost.

## Architecture

```
empirica/
├── core/scanner/                    # All collection lives here
│   ├── snapshot.py                  # Snapshot dataclass + collect_snapshot orchestrator
│   ├── read_surface.py              # YAML parser + universe-intersection
│   ├── processes.py                 # psutil process_iter + per-row read-surface filter
│   ├── network.py                   # psutil net_connections, listening + established
│   ├── scheduled.py                 # crontab + systemd-user + launchd
│   ├── env_names.py                 # env var NAMES only (no values, ever)
│   ├── manifests.py                 # plugin.json + ~/.claude/mcp.json
│   └── report.py                    # Markdown renderer
├── data/security-corpus/            # 5 bundled stubs (used by Phase 2)
└── cli/parsers/scan_parsers.py
└── cli/command_handlers/scan_commands.py
```

## Read-surface — the agent's read permission boundary

`cockpit.scanner.read_surface` in `.empirica/project.yaml` declares
which fields each collector is allowed to emit. The scanner intersects
the user's YAML with a hard-coded universe per collector, so a typo or
stray entry can never silently widen the surface.

```yaml
cockpit:
  scanner:
    read_surface:
      process: [pid, cmdline, parent_pid, age_seconds, working_dir,
                num_open_files, cpu_percent, memory_mb, is_scanner_self]
      network: [pid, peer_host, peer_port, listening_ports,
                local_address, local_port, status]
      filesystem: [plugin_manifest_paths, recently_touched_model_weights,
                   env_files_present]
      process_env: [var_names_only]   # the only legal emission — values never read
      scheduled: [cron_entries, systemd_user_units, launchd_agents]
      mcp: [registered_servers, active_connections]
    relevant_globs_for_coverage:
      code:  ["empirica/**/*.py"]
      docs:  ["docs/**/*.md"]
      audit: ["**/*.json"]
```

If the block is absent, the default surface (everything in the proposal)
is used. Projects that want a narrower surface override only the
relevant collector lists.

## Two coverage concepts — keep them distinct

Coverage is the methodological pillar this product rides on, but the
word means two different things at different layers. Phase 1 ships the
first; Phase 2 layers the second on top of the same `relevant_globs`
substrate.

| Concept | Question | Phase | What it grounds |
|---|---|---|---|
| **Scanner integrity coverage** | _"Did the deterministic collector successfully capture every row available to it?"_ | Phase 1 (now) | Snapshot truthfulness — were 491 of 491 processes read, or did permission denials skip some? |
| **Agent self-coverage** | _"Of the relevant material that exists, how much did the AI agent actually inspect before claiming its confidence?"_ | Phase 2 (next) | Human trust — divide claimed confidence by reading coverage to get an effective trust score |

Both surface in the snapshot's `coverage` block. Phase 1 fills in the
integrity metrics (`processes.attempted/succeeded/ratio`,
`network.attempted/succeeded/ratio`, etc.) and counts files matching
`relevant_globs_for_coverage` patterns. Phase 2 will add a parallel
`agent_coverage` block recording which of those matched files the
auditor agent actually read, plus which of the 5 bundled corpus files
the agent cited from before emitting each finding.

The grounding ladder this enables (per the proposal):

- ≥ 0.95 confidence + cited corpus section → `finding`
- 0.6–0.95 confidence + cited → `assumption`
- < 0.6 confidence OR uncited → `unknown`

## Snapshot schema (Phase 1 deterministic only)

```jsonc
{
  "scan_id": "uuid",
  "started_at": 1746098451.93,
  "finished_at": 1746098456.94,
  "host": "machine-name",
  "platform": "Linux 6.17.0-22-generic",
  "scanner_pid": 2958649,
  "snapshot": {
    "processes": [{"pid": 2958649, "is_scanner_self": true, ...}, ...],
    "network": {"connections": [...], "listening_ports": [22, 53, ...]},
    "scheduled": {"cron_entries": [...], "systemd_user_units": [...], "launchd_agents": [...]},
    "process_env": {"var_names_only": ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", ...]},
    "filesystem": {"plugin_manifest_paths": [...], "mcp_registered_servers": [...]},
    "coverage": {
      "processes": {"attempted": 491, "succeeded": 491, "ratio": 1.0, ...},
      "network": {"attempted": 143, "succeeded": 143, "ratio": 1.0, ...},
      "scheduled": {"sources_checked": 3, "sources_yielding": 1, "total_entries": 2},
      "process_env": {"total_env_vars": 104, "interesting_matches": 9, "ratio": 0.087},
      "filesystem": {...},
      "relevant_globs": {
        "code": {"patterns": {"empirica/**/*.py": 404}, "total_matches": 404},
        "docs": {"patterns": {"docs/**/*.md": 210}, "total_matches": 210}
      }
    }
  },
  "errors": []
}
```

Persistence (when `--save` is passed):

| Path | Purpose |
|---|---|
| `~/.empirica/scans/<scan_id>.json` | Full snapshot |
| `~/.empirica/last_scan_<project_id>.json` | Latest scan, for cockpit |
| `~/.empirica/scan_history_<project_id>.jsonl` | Append-only audit trail (one line per scan, summary-only) |

## Bundled security corpus

Five stub markdown files at `empirica/data/security-corpus/`:

- `owasp-llm-top10.md`
- `owasp-agentic-top10.md`
- `nist-ai-rmf.md`
- `mitre-atlas.md`
- `google-saif.md`

Phase 1 ships them as title + canonical URL + section-header skeleton.
The Phase 2 services-auditor will cite their section IDs (`Agentic-A06`,
`MEASURE-2.7`, `T1499`, etc.) — those IDs are stable across revisions
even when section bodies are stubs. Real content fills in via the
Phase 3 monthly corpus-refresh loop.

## Privacy posture

- **Process env values:** never read. Only names that match conventional
  AI / secret patterns (`*_API_KEY`, `*_TOKEN`, `OPENAI*`, etc.) appear
  in the snapshot.
- **Network:** metadata only — connection 5-tuple + listening ports.
  No packet contents, no header inspection.
- **Filesystem:** paths only — `.env` file presence, plugin manifest
  locations, MCP server registry. File contents never read.
- **Scheduled tasks:** crontab / unit / plist filenames only —
  `crontab -l` lines are recorded verbatim because they are the user's
  own configuration; systemd unit and launchd plist contents never read.

## Out of scope for Phase 1

| | Why deferred |
|---|---|
| AI judgment of "is this dangerous" | Phase 2 — needs the auditor persona + corpus content |
| Multi-host fleet view | Separate product (`empirica fleet`) |
| Hosted-agent inventory (cloud operators on your account) | Phase 4+ — needs API token introspection |
| Network packet inspection | Explicit non-goal — read-only on metadata only |
| Action layer that kills processes | Read-only by design; recommended_action strings only (Phase 2) |

## Acceptance criteria — Phase 1

- [x] `empirica scan` produces structured JSON snapshot of running processes + sockets + scheduled tasks + env-var names + plugin manifests
- [x] Read-surface YAML in `project.yaml` honored (universe-intersected)
- [x] Markdown report renders cleanly for human consumption
- [x] Scanner shows itself in its own output (self-row tagged)
- [x] No persistent daemon (one-shot only)
- [x] Tests cover: snapshot shape, read-surface enforcement, scanner-self-detection, env-value-leak prevention, coverage roll-up
- [x] `docs/architecture/SERVICES_SCANNER.md` written (this file)
- [x] CHANGELOG entry under `[Unreleased]`

Phase 1 does not yet wire the `services-auditor` persona, ntfy alerts,
the cockpit `#services` panel, the biweekly loop, or `--explain`. Those
are Phase 2 / Phase 3.
