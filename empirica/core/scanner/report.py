"""Markdown report renderer for scanner snapshots.

Phase 1 — deterministic only. Coverage block leads the report so a human
reading the output sees immediately how complete the snapshot was. Phase 2
will append a findings section after coverage.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .snapshot import Snapshot


def _ts(epoch: float | None) -> str:
    if not epoch:
        return '—'
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc).isoformat(timespec='seconds')


def _percent(ratio: float | int | None) -> str:
    if ratio is None:
        return '—'
    try:
        return f"{float(ratio) * 100:.1f}%"
    except (TypeError, ValueError):
        return '—'


def _coverage_section(coverage: dict[str, Any]) -> list[str]:
    """Render the coverage block — the first thing the reader sees."""
    out = ["## Coverage", ""]

    proc = coverage.get('processes', {})
    out.append(
        f"- **Processes:** {proc.get('succeeded', 0)}/{proc.get('attempted', 0)} "
        f"({_percent(proc.get('ratio'))})"
    )
    if proc.get('skip_reasons'):
        out.append(f"  - skip reasons: {proc['skip_reasons']}")

    net = coverage.get('network', {})
    out.append(
        f"- **Network sockets:** {net.get('succeeded', 0)}/{net.get('attempted', 0)} "
        f"({_percent(net.get('ratio'))})"
    )
    if net.get('skip_reasons'):
        out.append(f"  - skip reasons: {net['skip_reasons']}")

    sched = coverage.get('scheduled', {})
    out.append(
        f"- **Scheduled tasks:** {sched.get('total_entries', 0)} entries from "
        f"{sched.get('sources_yielding', 0)}/{sched.get('sources_checked', 0)} sources"
    )

    env_cov = coverage.get('process_env', {})
    out.append(
        f"- **Env-var names (interesting / total):** "
        f"{env_cov.get('interesting_matches', 0)}/{env_cov.get('total_env_vars', 0)} "
        f"({_percent(env_cov.get('ratio'))})"
    )

    fs = coverage.get('filesystem', {})
    out.append(
        f"- **Filesystem:** {fs.get('plugin_manifests_found', 0)} plugin manifests, "
        f"{fs.get('mcp_registered_servers', 0)} MCP servers, "
        f"{fs.get('env_files_found', 0)} .env files"
    )

    globs = coverage.get('relevant_globs') or {}
    if globs:
        out.append("- **Relevant-globs (configured in `cockpit.scanner.relevant_globs_for_coverage`):**")
        for category, info in globs.items():
            out.append(
                f"  - `{category}`: {info.get('total_matches', 0)} files matching "
                f"{len(info.get('patterns') or {})} pattern(s)"
            )
    else:
        out.append(
            "- **Relevant-globs:** _no `cockpit.scanner.relevant_globs_for_coverage` "
            "configured — Phase 2 judgment will need this to compute reading coverage._"
        )

    out.append("")
    return out


def _process_table(processes: list[dict[str, Any]], limit: int = 20) -> list[str]:
    if not processes:
        return ["_(no processes captured — psutil unavailable or iter failed)_", ""]

    # Surface scanner-self first, then top by memory
    self_rows = [p for p in processes if p.get('is_scanner_self')]
    other_rows = [p for p in processes if not p.get('is_scanner_self')]
    other_rows.sort(key=lambda p: p.get('memory_mb') or 0, reverse=True)
    sample = self_rows + other_rows[:max(0, limit - len(self_rows))]

    lines = [
        f"_Showing {len(sample)} of {len(processes)} processes "
        f"(scanner-self + top {limit - len(self_rows)} by memory)._",
        "",
        "| PID | Self | Mem MB | Age (s) | Cmdline |",
        "|---|---|---|---|---|",
    ]
    for row in sample:
        self_marker = '🔍' if row.get('is_scanner_self') else ''
        cmd = (row.get('cmdline') or '').replace('|', '¦')
        if len(cmd) > 80:
            cmd = cmd[:77] + '…'
        lines.append(
            f"| {row.get('pid', '?')} | {self_marker} | "
            f"{row.get('memory_mb', '—')} | {row.get('age_seconds', '—')} | {cmd} |"
        )
    lines.append("")
    return lines


def _network_section(network: dict[str, Any]) -> list[str]:
    out = ["## Network", ""]
    listening = network.get('listening_ports', [])
    if listening:
        out.append(f"**Listening ports ({len(listening)}):** "
                   + ', '.join(str(p) for p in listening[:30])
                   + (" …" if len(listening) > 30 else ""))
    else:
        out.append("_No listening ports observed (or insufficient permissions)._")
    conns = network.get('connections', [])
    out.append("")
    out.append(f"**Connections seen:** {len(conns)} (of which "
               f"{sum(1 for c in conns if c.get('peer_host'))} have a peer endpoint).")
    out.append("")
    return out


def _scheduled_section(scheduled: dict[str, Any]) -> list[str]:
    out = ["## Scheduled tasks", ""]

    cron = scheduled.get('cron_entries', [])
    out.append(f"**Crontab entries:** {len(cron)}")
    for entry in cron[:10]:
        out.append(f"  - `{entry.get('line', '')}`")

    units = scheduled.get('systemd_user_units', [])
    out.append("")
    out.append(f"**systemd user units:** {len(units)}")
    for entry in units[:10]:
        out.append(f"  - `{entry.get('name', '?')}`")

    agents = scheduled.get('launchd_agents', [])
    out.append("")
    out.append(f"**launchd agents:** {len(agents)}")
    for entry in agents[:10]:
        out.append(f"  - `{entry.get('label', '?')}`")
    out.append("")
    return out


def _filesystem_section(filesystem: dict[str, Any]) -> list[str]:
    out = ["## Filesystem & MCP", ""]

    plugins = filesystem.get('plugin_manifest_paths', [])
    out.append(f"**Plugin manifests ({len(plugins)}):**")
    for path in plugins[:10]:
        out.append(f"  - `{path}`")
    if len(plugins) > 10:
        out.append(f"  - …{len(plugins) - 10} more")

    mcp = filesystem.get('mcp_registered_servers', [])
    out.append("")
    out.append(f"**Registered MCP servers ({len(mcp)}):**")
    for entry in mcp[:20]:
        out.append(
            f"  - `{entry.get('name', '?')}` "
            f"(command={entry.get('command') or '—'}, args={entry.get('args_count', 0)})"
        )

    env_files = filesystem.get('env_files_present', [])
    out.append("")
    if env_files:
        out.append(f"**.env files in this project root:** {len(env_files)}")
        for path in env_files:
            out.append(f"  - `{path}`")
    else:
        out.append("**.env files in this project root:** none")
    out.append("")
    return out


def _env_section(process_env: dict[str, Any], cov: dict[str, Any]) -> list[str]:
    names = process_env.get('var_names_only', [])
    out = ["## Process environment (names only — values never read)", ""]
    if not names:
        out.append("_No interesting env-var names matched the filter._")
        out.append("")
        return out
    out.append(
        f"_Showing {len(names)} of {cov.get('total_env_vars', '?')} total env vars._"
    )
    for name in names:
        out.append(f"  - `{name}`")
    out.append("")
    return out


def render_markdown(snapshot: Snapshot) -> str:
    """Render a :class:`Snapshot` as Markdown for human consumption."""
    coverage = snapshot.snapshot.get('coverage', {})
    elapsed = (
        f"{snapshot.finished_at - snapshot.started_at:.2f}s"
        if snapshot.finished_at else "in progress"
    )

    lines: list[str] = [
        f"# empirica scan — {_ts(snapshot.started_at)}",
        "",
        f"- **scan_id:** `{snapshot.scan_id}`",
        f"- **host:** `{snapshot.host}`",
        f"- **platform:** {snapshot.platform}",
        f"- **scanner_pid:** {snapshot.scanner_pid} _(self-row tagged in process table)_",
        f"- **duration:** {elapsed}",
    ]
    if snapshot.errors:
        lines.append(f"- **collector errors:** {len(snapshot.errors)}")
    lines.append("")

    lines.extend(_coverage_section(coverage))
    lines.append("---")
    lines.append("")

    lines.append("## Processes")
    lines.append("")
    lines.extend(_process_table(snapshot.processes))
    lines.extend(_network_section(snapshot.snapshot.get('network', {})))
    lines.extend(_scheduled_section(snapshot.snapshot.get('scheduled', {})))
    lines.extend(_filesystem_section(snapshot.snapshot.get('filesystem', {})))
    lines.extend(_env_section(
        snapshot.snapshot.get('process_env', {}),
        coverage.get('process_env', {}),
    ))

    if snapshot.errors:
        lines.append("## Collector errors")
        lines.append("")
        for err in snapshot.errors:
            lines.append(f"- `{err}`")
        lines.append("")

    lines.append("---")
    lines.append("_Phase 1 deterministic snapshot — no AI judgment yet (Phase 2 adds findings)._")
    return '\n'.join(lines) + '\n'


__all__ = ['render_markdown']
