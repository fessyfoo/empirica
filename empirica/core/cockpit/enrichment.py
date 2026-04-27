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
    context: float | None
    completion: float | None
    confidence: float | None  # composite score 0.0-1.0
    open_goals: int | None
    artifact_count: int | None  # alias for open_goals; kept for backwards compat
    raw: dict[str, Any] | None


def calculate_confidence(vectors: dict[str, Any]) -> float:
    """Composite confidence from epistemic vectors.

    Mirrors statusline_empirica.calculate_confidence (kept independent so
    the cockpit doesn't depend on the plugin scripts directory):
      0.40 × know
      0.30 × (1 - uncertainty)
      0.20 × context
      0.10 × completion

    Returns 0.0-1.0. Defaults missing vectors to 0.5 (uncertainty/context)
    or 0.0 (completion).
    """
    if not vectors:
        return 0.0
    know = float(vectors.get('know', 0.5) or 0.5)
    uncertainty = float(vectors.get('uncertainty', 0.5) or 0.5)
    context = float(vectors.get('context', 0.5) or 0.5)
    completion = float(vectors.get('completion', 0.0) or 0.0)
    score = 0.40 * know + 0.30 * (1.0 - uncertainty) + 0.20 * context + 0.10 * completion
    return max(0.0, min(1.0, score))


def statusline_summary(
    instance_id: str,
    label_fallback: str | None = None,
    project_path: str | None = None,
    session_id: str | None = None,
) -> StatuslineSummary:  # noqa: D401
    """(see body) — the open_goals count is project-scoped to match the
    open_goals_list widget below it."""
    """Resolve the live statusline summary for an instance.

    Source priority:
      1. project sessions.db epistemic_snapshots (live, real-time vectors)
      2. ~/.empirica/statusline_cache/{instance_id}_*.json (legacy cache)
    """
    blank = StatuslineSummary(
        instance_id=instance_id, found=False, label=label_fallback,
        know=None, uncertainty=None, context=None, completion=None,
        confidence=None, open_goals=None, artifact_count=None, raw=None,
    )

    if project_path and session_id:
        live = _live_statusline_from_db(project_path, session_id, label_fallback)
        if live is not None:
            return live

    return _statusline_from_cache(instance_id, label_fallback) or blank


def _live_statusline_from_db(
    project_path: str, session_id: str, label_fallback: str | None,
) -> StatuslineSummary | None:
    """Pull the most-recent vectors + open-goal count from project DB.

    Open-goal count mirrors statusline_empirica.get_open_counts:
      WHERE is_completed = 0 AND project_id = ?

    `is_completed` is the source of truth (the `status` column has
    historical inconsistencies); `project_id` filter scopes to the
    current project, excluding goals from prior project_id values left
    over from test runs or schema migrations.
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

        # Resolve project_id from sessions table for accurate goal scoping.
        project_id: str | None = None
        try:
            cur.execute('SELECT project_id FROM sessions WHERE session_id = ?', (session_id,))
            row_pid = cur.fetchone()
            if row_pid and row_pid[0]:
                project_id = str(row_pid[0])
        except sqlite3.Error:
            pass

        open_goals = _count_open_goals(cur, project_id)

        conn.close()
    except sqlite3.Error:
        return None

    if not row or not row[0]:
        return None
    try:
        vectors = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None

    confidence = calculate_confidence(vectors)
    return StatuslineSummary(
        instance_id='',
        found=True,
        label=label_fallback,
        know=_safe_float(vectors.get('know')),
        uncertainty=_safe_float(vectors.get('uncertainty')),
        context=_safe_float(vectors.get('context')),
        completion=_safe_float(vectors.get('completion')),
        confidence=round(confidence, 2),
        open_goals=open_goals,
        artifact_count=open_goals,
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
    open_goals = data.get('open_goals')
    if open_goals is None:
        open_goals = (data.get('open_goals') or 0) + (data.get('open_unknowns') or 0)
    confidence = calculate_confidence(vectors) if vectors else None
    return StatuslineSummary(
        instance_id=instance_id,
        found=True,
        label=label,
        know=_safe_float(vectors.get('know')),
        uncertainty=_safe_float(vectors.get('uncertainty')),
        context=_safe_float(vectors.get('context')),
        completion=_safe_float(vectors.get('completion')),
        confidence=round(confidence, 2) if confidence is not None else None,
        open_goals=int(open_goals) if open_goals is not None else None,
        artifact_count=int(open_goals) if open_goals is not None else None,
        raw={**data, 'source': 'statusline_cache'},
    )


def _safe_float(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


# ─── open goals ────────────────────────────────────────────────────────────

@dataclass
class OpenGoal:
    objective: str
    status: str  # 'in_progress' | 'blocked'
    age_seconds: float | None


def open_goals_list(
    project_path: str | None,
    session_id: str | None = None,
    limit: int = 5,
) -> list[OpenGoal]:
    """Return the N most recent open goals for the current project.

    Mirrors statusline_empirica.get_open_counts logic:
      WHERE is_completed = 0 AND project_id = ?

    project_id is resolved from the `sessions` table via the passed
    session_id. If session_id is None or no project_id can be resolved,
    falls back to project-wide is_completed=0 (still excludes 'completed'
    goals via the boolean source-of-truth).

    `is_completed` is the canonical column for done-ness; the textual
    `status` column has historical inconsistencies. Sorted newest first.
    """
    if not project_path:
        return []
    nested = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
    bare = Path(project_path) / '.empirica' / 'sessions.db'
    if nested.exists() and nested.stat().st_size > 0:
        db_path = nested
    elif bare.exists() and bare.stat().st_size > 0:
        db_path = bare
    else:
        return []

    goals: list[OpenGoal] = []
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1.0)
        cur = conn.cursor()
        project_id = _resolve_project_id(cur, session_id) if session_id else None
        if project_id:
            cur.execute(
                "SELECT objective, status, created_timestamp FROM goals "
                "WHERE is_completed = 0 AND project_id = ? "
                "ORDER BY created_timestamp DESC LIMIT ?",
                (project_id, limit),
            )
        else:
            cur.execute(
                "SELECT objective, status, created_timestamp FROM goals "
                "WHERE is_completed = 0 "
                "ORDER BY created_timestamp DESC LIMIT ?",
                (limit,),
            )
        now = datetime.now(tz=UTC).timestamp()
        for objective, status, created in cur.fetchall():
            try:
                age = max(0.0, now - float(created)) if created is not None else None
            except (TypeError, ValueError):
                age = None
            goals.append(OpenGoal(
                objective=str(objective or ''),
                status=str(status or 'in_progress'),
                age_seconds=age,
            ))
        conn.close()
    except sqlite3.Error:
        return []
    return goals


def _resolve_project_id(cur: sqlite3.Cursor, session_id: str) -> str | None:
    """Look up project_id for a session. Returns None if not found or table absent."""
    try:
        cur.execute('SELECT project_id FROM sessions WHERE session_id = ?', (session_id,))
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0])
    except sqlite3.Error:
        pass
    return None


def _count_open_goals(cur: sqlite3.Cursor, project_id: str | None) -> int | None:
    """Count goals with is_completed = 0 (canonical source of truth).

    Project-scoped when project_id is available; otherwise project-wide.
    """
    try:
        if project_id:
            cur.execute(
                'SELECT COUNT(*) FROM goals WHERE is_completed = 0 AND project_id = ?',
                (project_id,),
            )
        else:
            cur.execute('SELECT COUNT(*) FROM goals WHERE is_completed = 0')
        row = cur.fetchone()
        if row:
            return int(row[0])
    except sqlite3.Error:
        pass
    return None


# ─── notifications list (placeholder companion to notification_summary) ────

@dataclass
class NotificationItem:
    title: str
    body: str | None
    received_iso: str | None
    source: str  # 'enp' | 'unknown'


def notifications_list(instance_id: str, limit: int = 5) -> list[NotificationItem]:
    """Return per-instance open notifications.

    PLACEHOLDER — reads ~/.empirica/enp/items_{id}.json if present (an
    expected schema laid out for future ENP→cockpit integration). Returns
    empty list otherwise. Ships an ergonomic empty-state in the TUI.
    """
    safe_id = instance_id.replace('/', '-').replace('%', '')
    path = EMPIRICA_DIR / 'enp' / f'items_{safe_id}.json'
    if not path.exists():
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    items_raw = data.get('items', []) if isinstance(data, dict) else []
    if not isinstance(items_raw, list):
        return []

    out: list[NotificationItem] = []
    for raw in items_raw[:limit]:
        if not isinstance(raw, dict):
            continue
        out.append(NotificationItem(
            title=str(raw.get('title', '(untitled)')),
            body=raw.get('body'),
            received_iso=raw.get('received'),
            source=str(raw.get('source', 'enp')),
        ))
    return out


# ─── context window usage (CC writes the file; cockpit reads it) ──────────

def context_usage(instance_id: str) -> int | None:
    """Read CC's context window usage % for this instance.

    Source: ~/.empirica/context_usage_{instance_id}.json (written by the
    statusline command when CC invokes it with stdin payload). Returns
    the integer percentage 0-100, or None if no recent file.
    """
    safe_id = instance_id.replace('/', '-').replace('%', '')
    path = EMPIRICA_DIR / f'context_usage_{safe_id}.json'
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        used = data.get('used_percentage')
        if used is None:
            return None
        return max(0, min(100, int(used)))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
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
    'NotificationItem',
    'NotificationSummary',
    'OpenGoal',
    'RecentAction',
    'StatuslineSummary',
    'calculate_confidence',
    'clear_notifications',
    'context_usage',
    'is_asking',
    'notification_summary',
    'notifications_list',
    'notifications_total',
    'open_goals_list',
    'recent_actions',
    'statusline_summary',
]
