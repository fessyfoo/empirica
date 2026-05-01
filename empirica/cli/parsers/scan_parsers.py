"""Argparse parser for `empirica scan` (Phase 1 — one-shot).

Per docs/architecture/PROPOSAL_AI_SERVICE_SCANNER.md.
"""

from __future__ import annotations


def add_scan_parsers(subparsers):
    """Register the `scan` verb."""
    scan_root = subparsers.add_parser(
        'scan',
        help='One-shot inventory of running AI-touching services (read-only)',
        description="""
Live running-state inventory: processes, sockets, scheduled tasks, plugin
manifests, MCP servers, env-var names. Phase 1 is deterministic data
collection only — no AI judgment yet. Markdown output by default; JSON
for piping. Read-surface declared in project.yaml under
cockpit.scanner.read_surface (defaults applied if absent).
        """,
    )
    scan_root.add_argument('--output', choices=['markdown', 'json'],
                           default='markdown',
                           help='Output format (default: markdown)')
    scan_root.add_argument('--save', action='store_true',
                           help='Persist the JSON snapshot to ~/.empirica/scans/<scan_id>.json '
                                'and update last_scan_<project_id>.json for cockpit consumption')
    scan_root.add_argument('--project-id',
                           help='Project UUID (overrides automatic resolution)')
