"""Handler for `empirica scan` (Phase 1 — one-shot).

Per docs/architecture/PROPOSAL_AI_SERVICE_SCANNER.md.

The handler is intentionally thin: it owns format choice, optional
persistence to ``~/.empirica/scans/``, and exit code semantics. All data
collection lives in :mod:`empirica.core.scanner`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from empirica.core.scanner import collect_snapshot
from empirica.core.scanner.report import render_markdown

logger = logging.getLogger(__name__)


def _empirica_home() -> Path:
    home = Path(os.path.expanduser('~/.empirica'))
    home.mkdir(parents=True, exist_ok=True)
    return home


def _persist_scan(snapshot_dict: dict, project_id: str | None) -> dict[str, str]:
    """Write the snapshot to disk for cockpit consumption.

    Returns a small dict with the paths written, so the JSON output can
    surface them.
    """
    paths: dict[str, str] = {}
    home = _empirica_home()

    scans_dir = home / 'scans'
    scans_dir.mkdir(parents=True, exist_ok=True)
    scan_path = scans_dir / f"{snapshot_dict['scan_id']}.json"
    with scan_path.open('w', encoding='utf-8') as fh:
        json.dump(snapshot_dict, fh, indent=2, default=str)
    paths['scan_path'] = str(scan_path)

    if project_id:
        last_path = home / f"last_scan_{project_id}.json"
        with last_path.open('w', encoding='utf-8') as fh:
            json.dump(snapshot_dict, fh, indent=2, default=str)
        paths['last_scan_path'] = str(last_path)

        history_path = home / f"scan_history_{project_id}.jsonl"
        # Append a one-line summary per scan — keeps the audit trail cheap
        history_summary = {
            'scan_id': snapshot_dict['scan_id'],
            'started_at': snapshot_dict['started_at'],
            'finished_at': snapshot_dict.get('finished_at'),
            'host': snapshot_dict['host'],
            'coverage': snapshot_dict.get('snapshot', {}).get('coverage', {}),
            'errors': len(snapshot_dict.get('errors') or []),
        }
        with history_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(history_summary, default=str) + '\n')
        paths['history_path'] = str(history_path)

    return paths


def _resolve_project_id(args) -> str | None:
    project_id = getattr(args, 'project_id', None)
    if project_id:
        return project_id
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        path = R.project_path()
        if path:
            return R.project_id_from_db(path)
    except Exception:
        return None
    return None


def handle_scan_command(args) -> int:
    """`empirica scan` — emit a deterministic inventory snapshot."""
    output_format = getattr(args, 'output', 'markdown')
    save = getattr(args, 'save', False)
    project_id = _resolve_project_id(args)

    try:
        snapshot = collect_snapshot()
    except Exception as exc:
        logger.error(f"scan failed: {exc}")
        if output_format == 'json':
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"❌ scan failed: {exc}")
        return 1

    snapshot_dict = snapshot.to_dict()
    saved_paths: dict[str, str] = {}
    if save:
        try:
            saved_paths = _persist_scan(snapshot_dict, project_id)
        except OSError as exc:
            logger.warning(f"scan persistence failed: {exc}")
            saved_paths = {'error': str(exc)}

    if output_format == 'json':
        envelope = {
            'ok': True,
            'project_id': project_id,
            'snapshot': snapshot_dict,
            'saved': saved_paths if save else None,
        }
        print(json.dumps(envelope, indent=2, default=str))
    else:
        # Markdown
        markdown = render_markdown(snapshot)
        print(markdown)
        if save and saved_paths.get('scan_path'):
            print(f"_Saved to {saved_paths['scan_path']}_")

    return 0
