"""get_file_relevant_artifacts — surface artifacts that mention a file path.

Called by sentinel-gate.py PreToolUse hook on Edit/Write/MultiEdit so the
AI sees what's already known about the file before modifying it.

This closes the gap where the AI edits a file without first checking
whether prior findings, decisions, dead-ends, or mistakes referenced it.

Lookup is a SQLite LIKE search on the artifact text columns of all six
artifact tables, scoped to the active project. Both the basename and
the project-relative path are matched so 'auth.py' and
'src/auth/auth.py' both hit the same artifacts.

Hot-path constraint: the hook runs on every PreToolUse, so this stays
under ~50ms by capping per-table queries at small limits and using a
single sqlite connection.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# (table, id_col, primary_text_col, type_label, secondary_cols, ts_col)
_ARTIFACT_TABLES: list[tuple[str, str, str, str, list[str], str]] = [
    ("project_findings", "id", "finding", "finding", [], "created_timestamp"),
    ("project_unknowns", "id", "unknown", "unknown", [], "created_timestamp"),
    ("project_dead_ends", "id", "approach", "dead_end", ["why_failed"], "created_timestamp"),
    ("mistakes_made", "id", "mistake", "mistake", ["why_wrong"], "created_timestamp"),
    ("assumptions", "id", "assumption", "assumption", [], "created_timestamp"),
    ("decisions", "id", "choice", "decision", ["rationale"], "created_timestamp"),
]


def get_file_relevant_artifacts(
    project_path: str | Path,
    file_path: str,
    *,
    project_id: str | None = None,
    limit: int = 5,
    per_table_cap: int = 3,
) -> list[dict[str, Any]]:
    """Return artifacts whose text content mentions `file_path`.

    Args:
        project_path: Project root containing .empirica/sessions/sessions.db.
        file_path: Absolute or relative path the AI is about to modify.
        project_id: Optional project UUID. If None, search is unscoped
            (across the project's sessions DB which is already
            project-scoped — safe).
        limit: Maximum total candidates returned (default 5).
        per_table_cap: Per-artifact-type cap before merging (default 3).

    Returns:
        List of dicts shaped:
          {"id": uuid, "type": one of finding|unknown|dead_end|mistake|
                                       assumption|decision,
           "summary": str ≤120,
           "created_at": ISO timestamp string or None}
        Sorted by recency descending (newest first). Empty list on any
        failure path (missing DB, unreadable, no matches).
    """
    if not project_path or not file_path:
        return []

    project_root = Path(project_path)
    db_path = project_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return []

    needles = _build_needles(project_root, file_path)
    if not needles:
        return []

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    out: list[dict] = []

    try:
        for table, id_col, primary, type_label, secondary, ts_col in _ARTIFACT_TABLES:
            rows = _query_table(cur, table, id_col, primary, secondary, ts_col, project_id, needles, per_table_cap)
            for row in rows:
                summary = ""
                for value in row[1:-1]:
                    if value:
                        summary = str(value)[:120]
                        break
                created_at = _to_iso(row[-1])
                out.append(
                    {
                        "id": row[0],
                        "type": type_label,
                        "summary": summary,
                        "created_at": created_at,
                        "_ts": row[-1] or 0,
                    }
                )
    finally:
        conn.close()

    out.sort(key=lambda h: h.get("_ts") or 0, reverse=True)
    for item in out:
        item.pop("_ts", None)
    return out[:limit]


def format_relevance_nudge(artifacts: list[dict]) -> str:
    """Format the artifact list as a one-line sentinel nudge string.

    Empty input → empty string (the hook then skips the nudge entirely).
    """
    if not artifacts:
        return ""

    counts: dict[str, int] = {}
    for art in artifacts:
        counts[art["type"]] = counts.get(art["type"], 0) + 1

    parts = []
    type_order = ("finding", "decision", "dead_end", "mistake", "assumption", "unknown")
    for t in type_order:
        if t in counts:
            label = t.replace("_", "-")
            parts.append(f"{counts[t]} {label}{'s' if counts[t] > 1 else ''}")

    summary = ", ".join(parts)
    return (
        f"FILE-RELEVANCE: {summary} reference this file. "
        f'Run `empirica project-search --task "<file>"` '
        f"or check related artifacts before overwriting prior knowledge."
    )


# ── Helpers ────────────────────────────────────────────────────────────


def _build_needles(project_root: Path, file_path: str) -> list[str]:
    """Build LIKE patterns to match against artifact text.

    Returns the basename and the project-relative path. Both are
    distinguishing enough for typical artifact references; we skip
    the absolute path because it's user-/machine-specific.
    """
    needles: list[str] = []
    fp = Path(file_path)
    base = fp.name
    if base and len(base) >= 3:
        needles.append(base)

    try:
        rel = os.path.relpath(file_path, project_root)
    except ValueError:
        rel = ""
    if rel and rel != base and not rel.startswith(".."):
        needles.append(rel)

    return needles


def _query_table(
    cur: sqlite3.Cursor,
    table: str,
    id_col: str,
    primary: str,
    secondary: list[str],
    ts_col: str,
    project_id: str | None,
    needles: list[str],
    cap: int,
) -> list[tuple]:
    """Run the LIKE search for one artifact table."""
    text_cols = [primary] + secondary
    cols_select = ", ".join([id_col] + text_cols + [ts_col])

    like_clauses = []
    params: list[Any] = []
    for needle in needles:
        for col in text_cols:
            like_clauses.append(f"{col} LIKE ?")
            params.append(f"%{needle}%")

    where_parts = ["(" + " OR ".join(like_clauses) + ")"]
    if project_id:
        where_parts.append("project_id = ?")
        params.append(project_id)

    sql = f"SELECT {cols_select} FROM {table} WHERE {' AND '.join(where_parts)} ORDER BY {ts_col} DESC LIMIT ?"
    params.append(cap)

    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except sqlite3.OperationalError as e:
        # Table or column missing on a pre-migration DB — skip silently
        logger.debug(f"file_relevance: query failed on {table}: {e}")
        return []


def _to_iso(value: Any) -> str | None:
    """Normalize a created_timestamp value (REAL epoch or already-ISO str)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None
