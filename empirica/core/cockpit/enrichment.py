"""Cockpit enrichment — secondary data sources for the compact TUI.

These four readers feed the fixed widgets at the bottom of the compact
TUI (statusline summary, recent actions list) and the per-row columns
(phase ∈ {noetic, praxic, ask, closed}, notif count).

All four are bounded I/O — single file read or single SQL query — so
they're safe to call on every refresh tick. Sources:

  asking_state    : ~/.empirica/asking_{instance_id}     (placeholder file)
  notif_count     : ~/.empirica/enp/open_{instance_id}.json  (placeholder)
  statusline      : ~/.empirica/statusline_cache/{instance_id}_*.json
  recent_actions  : <project>/.empirica/sessions.db   epistemic_events table
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / '.empirica'


# ─── ask state ─────────────────────────────────────────────────────────────

def is_asking(instance_id: str) -> bool:
    """True if the instance is currently waiting for user input.

    Reads ~/.empirica/asking_{instance_id} — a flag file written by a
    Notification hook in the CC plugin. Empty file existence == asking.

    The hook that writes this is a follow-up — until it ships, this
    always returns False, which keeps the column blank rather than wrong.
    """
    safe_id = instance_id.replace('/', '-').replace('%', '')
    return (EMPIRICA_DIR / f'asking_{safe_id}').exists()


# ─── notifications (placeholder) ───────────────────────────────────────────

@dataclass
class NotificationSummary:
    """Per-instance notification count.

    PLACEHOLDER — reads ~/.empirica/enp/open_{id}.json if it exists,
    returns zero counts otherwise. ENP→cockpit integration spec is owned
    by the empirica-extension Claude (see goal logged in this transaction).
    """
    instance_id: str
    open_count: int
    has_attention: bool


def notification_summary(instance_id: str) -> NotificationSummary:
    safe_id = instance_id.replace('/', '-').replace('%', '')
    path = EMPIRICA_DIR / 'enp' / f'open_{safe_id}.json'
    if not path.exists():
        return NotificationSummary(instance_id=instance_id, open_count=0, has_attention=False)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        open_count = int(data.get('open_count', 0) or 0)
        has_attention = bool(data.get('has_attention', open_count > 0))
        return NotificationSummary(
            instance_id=instance_id, open_count=open_count, has_attention=has_attention,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return NotificationSummary(instance_id=instance_id, open_count=0, has_attention=False)


def notifications_total() -> int:
    """Sum across all per-instance enp files. Cheap when the dir is small."""
    enp_dir = EMPIRICA_DIR / 'enp'
    if not enp_dir.exists():
        return 0
    total = 0
    for path in enp_dir.glob('open_*.json'):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            total += int(data.get('open_count', 0) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return total


def clear_notifications(instance_id: str) -> int:
    """Mark all per-instance notifications as cleared. Returns count cleared.

    PLACEHOLDER — currently just unlinks the open_{id}.json file. Real
    implementation will need to call out to ntfy archive endpoint and the
    empirica-extension API (see goal). Until then, clear is local-only.
    """
    safe_id = instance_id.replace('/', '-').replace('%', '')
    path = EMPIRICA_DIR / 'enp' / f'open_{safe_id}.json'
    if not path.exists():
        return 0
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        cleared = int(data.get('open_count', 0) or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        cleared = 0
    try:
        path.unlink()
    except OSError:
        pass
    return cleared


# ─── statusline summary ────────────────────────────────────────────────────

@dataclass
class StatuslineSummary:
    instance_id: str
    found: bool
    label: str | None
    know: float | None
    uncertainty: float | None
    artifact_count: int | None
    raw: dict[str, Any] | None


def statusline_summary(
    instance_id: str,
    label_fallback: str | None = None,
    project_path: str | None = None,
    session_id: str | None = None,
) -> StatuslineSummary:
    """Resolve the live statusline summary for an instance.

    Source priority:
      1. project sessions.db epistemic_snapshots (live, real-time vectors)
         — same source the empirica statusline reads
      2. ~/.empirica/statusline_cache/{instance_id}_*.json (legacy cache,
         can be stale)

    Returns a blank summary if neither source has data.
    """
    blank = StatuslineSummary(
        instance_id=instance_id, found=False, label=label_fallback,
        know=None, uncertainty=None, artifact_count=None, raw=None,
    )

    # Live DB read: most recent epistemic_snapshot for this instance's session.
    if project_path and session_id:
        live = _live_statusline_from_db(project_path, session_id, label_fallback)
        if live is not None:
            return live

    # Cache fallback (kept for instances without a project binding yet).
    return _statusline_from_cache(instance_id, label_fallback) or blank


def _live_statusline_from_db(
    project_path: str, session_id: str, label_fallback: str | None,
) -> StatuslineSummary | None:
    """Pull the most-recent vectors from the project's sessions DB.

    Path resolution: prefer .empirica/sessions/sessions.db (current
    convention); fall back to .empirica/sessions.db (legacy). Some projects
    have both — the bare one is often a 0-byte stale file.
    """
    nested = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
    bare = Path(project_path) / '.empirica' / 'sessions.db'
    if nested.exists() and nested.stat().st_size > 0:
        db_path = nested
    elif bare.exists() and bare.stat().st_size > 0:
        db_path = bare
    else:
        return None

    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1.0)
        cur = conn.cursor()
        cur.execute(
            'SELECT vectors FROM epistemic_snapshots '
            'WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1',
            (session_id,),
        )
        row = cur.fetchone()

        # Open-goal count for the same session (best-effort; goals table
        # is per-project, filter on session for the active scope).
        artifact_count: int | None = None
        try:
            cur.execute(
                "SELECT COUNT(*) FROM goals WHERE session_id = ? AND status != 'completed'",
                (session_id,),
            )
            row2 = cur.fetchone()
            if row2:
                artifact_count = int(row2[0])
        except sqlite3.Error:
            pass

        conn.close()
    except sqlite3.Error:
        return None

    if not row or not row[0]:
        return None
    try:
        vectors = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None

    return StatuslineSummary(
        instance_id='',  # caller binds
        found=True,
        label=label_fallback,
        know=_safe_float(vectors.get('know')),
        uncertainty=_safe_float(vectors.get('uncertainty')),
        artifact_count=artifact_count,
        raw={'vectors': vectors, 'source': 'epistemic_snapshots'},
    )


def _statusline_from_cache(
    instance_id: str, label_fallback: str | None,
) -> StatuslineSummary | None:
    """Legacy cache fallback for instances without project/session binding."""
    safe_id = instance_id.replace('/', '-').replace('%', '')
    cache_dir = EMPIRICA_DIR / 'statusline_cache'
    if not cache_dir.exists():
        return None

    candidates = sorted(
        cache_dir.glob(f'{safe_id}_*.json'),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    if not candidates:
        return None

    try:
        with open(candidates[0], encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    vectors = data.get('vectors') or {}
    label = data.get('project_name') or label_fallback
    artifact_count = data.get('open_goals') or data.get('artifact_count')
    if artifact_count is None:
        artifact_count = (data.get('open_goals') or 0) + (data.get('open_unknowns') or 0)
    return StatuslineSummary(
        instance_id=instance_id,
        found=True,
        label=label,
        know=_safe_float(vectors.get('know')),
        uncertainty=_safe_float(vectors.get('uncertainty')),
        artifact_count=int(artifact_count) if artifact_count is not None else None,
        raw={**data, 'source': 'statusline_cache'},
    )


def _safe_float(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


# ─── recent actions ────────────────────────────────────────────────────────

@dataclass
class RecentAction:
    timestamp: float
    iso_time: str  # HH:MM
    kind: str  # 'preflight' | 'check' | 'postflight' | 'finding' | ...
    summary: str


def recent_actions(
    project_path: str | None,
    session_id: str | None,
    limit: int = 5,
) -> list[RecentAction]:
    """Return the N most recent epistemic events for this instance.

    Queries the project's sessions.db. Falls back to empty list on any
    error — the cockpit is decorative-friendly for this widget, never
    blocking on it.
    """
    if not project_path:
        return []
    # Same path resolution as _live_statusline_from_db.
    nested = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
    bare = Path(project_path) / '.empirica' / 'sessions.db'
    if nested.exists() and nested.stat().st_size > 0:
        db_path = nested
    elif bare.exists() and bare.stat().st_size > 0:
        db_path = bare
    else:
        return []

    actions: list[RecentAction] = []
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1.0)
        cur = conn.cursor()

        # Pull latest epistemic events. event_type values include
        # preflight_complete, check_complete, postflight_complete.
        try:
            if session_id:
                cur.execute(
                    'SELECT timestamp, event_type, data_json '
                    'FROM epistemic_events WHERE session_id = ? '
                    'ORDER BY timestamp DESC LIMIT ?',
                    (session_id, limit * 3),
                )
            else:
                cur.execute(
                    'SELECT timestamp, event_type, data_json '
                    'FROM epistemic_events ORDER BY timestamp DESC LIMIT ?',
                    (limit * 3,),
                )
            for ts, kind, raw in cur.fetchall():
                actions.append(_format_event_action(ts, kind, raw))
        except sqlite3.Error:
            pass
        conn.close()
    except sqlite3.Error:
        return []

    actions.sort(key=lambda a: a.timestamp, reverse=True)
    return actions[:limit]


def _format_event_action(ts: float | None, kind: str, raw: str | None) -> RecentAction:
    if not isinstance(ts, (int, float)):
        ts = 0.0
    iso = datetime.fromtimestamp(float(ts), tz=UTC).astimezone().strftime('%H:%M')
    summary = kind.replace('_complete', '').replace('_', ' ')
    if raw:
        try:
            data = json.loads(raw)
            decision = data.get('decision') or data.get('phase') or data.get('reasoning')
            if decision:
                summary = f'{summary}: {str(decision)[:40]}'
        except (json.JSONDecodeError, TypeError):
            pass
    return RecentAction(timestamp=float(ts), iso_time=iso, kind=kind, summary=summary)


__all__ = [
    'NotificationSummary',
    'RecentAction',
    'StatuslineSummary',
    'clear_notifications',
    'is_asking',
    'notification_summary',
    'notifications_total',
    'recent_actions',
    'statusline_summary',
]
