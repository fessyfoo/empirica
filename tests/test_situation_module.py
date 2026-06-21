"""Tests for empirica.core.bootstrap.situation.

The situation block carries compaction-recovery state:
  - active_transaction (in-flight PREFLIGHT)
  - active_goal (recency-aware: in_progress > planned, within tier most-recent)
  - last_praxic_action (most recent commit)
  - project ("<name> @ <branch>")
  - next_focus (subtask > goal-linked unknown > recent project unknown)

Both `SessionDatabase._build_situation` (CLI path) and
`build_bootstrap_payload` (daemon HTTP path) delegate here.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from empirica.core.bootstrap.situation import (
    _active_goal,
    _next_focus,
    build_situation,
)


def _build_project(tmp_path: Path, name: str = "sit-proj") -> tuple[Path, str]:
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".empirica").mkdir()
    db_dir = proj / ".empirica" / "sessions"
    db_dir.mkdir()
    project_uuid = str(uuid.uuid4())

    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, project_id TEXT, ai_id TEXT,
            created_timestamp REAL
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            objective TEXT NOT NULL,
            description TEXT, scope TEXT,
            status TEXT DEFAULT 'in_progress',
            is_completed INTEGER DEFAULT 0,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE subtasks (
            id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
            description TEXT, epistemic_importance REAL DEFAULT 0.5,
            status TEXT DEFAULT 'pending',
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY, project_id TEXT, goal_id TEXT,
            unknown TEXT NOT NULL,
            is_resolved INTEGER DEFAULT 0,
            created_timestamp REAL NOT NULL
        );
    """)
    conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)", (project_uuid, name))
    conn.commit()
    conn.close()
    return proj, project_uuid


def _add_goal(
    db_path: Path, project_id: str, *, objective: str, status: str, age_hours: float = 0, description: str | None = None
) -> str:
    gid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO goals (id, project_id, objective, description, status, "
        "is_completed, created_timestamp) VALUES (?,?,?,?,?,?,?)",
        (gid, project_id, objective, description, status, 0, time.time() - age_hours * 3600),
    )
    conn.commit()
    conn.close()
    return gid


def _add_unknown(
    db_path: Path, project_id: str, unknown: str, *, goal_id: str | None = None, age_hours: float = 0
) -> str:
    uid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_unknowns (id, project_id, goal_id, unknown, "
        "is_resolved, created_timestamp) VALUES (?,?,?,?,?,?)",
        (uid, project_id, goal_id, unknown, 0, time.time() - age_hours * 3600),
    )
    conn.commit()
    conn.close()
    return uid


# ── Picker: in_progress > planned (status rank) ─────────────────────────


def test_active_goal_in_progress_wins_over_planned_when_both_present(tmp_path):
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    in_prog = _add_goal(db, pid, objective="in-progress old", status="in_progress", age_hours=72)
    _planned_new = _add_goal(db, pid, objective="planned brand-new", status="planned", age_hours=0)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    picked = _active_goal(conn, pid)
    conn.close()
    assert picked is not None
    assert picked["id"] == in_prog
    assert picked["status"] == "in_progress"


# ── Picker: recency wins within a status tier ───────────────────────────


def test_active_goal_recency_wins_within_in_progress_tier(tmp_path):
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _old = _add_goal(db, pid, objective="old", status="in_progress", age_hours=72)
    new = _add_goal(db, pid, objective="new", status="in_progress", age_hours=1)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    picked = _active_goal(conn, pid)
    conn.close()
    assert picked["id"] == new


def test_active_goal_planned_picked_when_no_in_progress(tmp_path):
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _old_planned = _add_goal(db, pid, objective="old planned", status="planned", age_hours=72)
    new_planned = _add_goal(db, pid, objective="new planned", status="planned", age_hours=1)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    picked = _active_goal(conn, pid)
    conn.close()
    assert picked["id"] == new_planned
    assert picked["status"] == "planned"


def test_active_goal_returns_none_when_only_completed_goals(tmp_path):
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    gid = _add_goal(db, pid, objective="done", status="completed", age_hours=1)
    # Mark completed
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE goals SET is_completed=1 WHERE id=?", (gid,))
    conn.commit()
    conn.row_factory = sqlite3.Row
    picked = _active_goal(conn, pid)
    conn.close()
    assert picked is None


# ── next_focus: recency-aware fallback ──────────────────────────────────


def test_next_focus_picks_goal_linked_unknown_first(tmp_path):
    """Unknown linked to active goal wins over orphan project unknowns."""
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    gid = _add_goal(db, pid, objective="active", status="in_progress", age_hours=1)
    _add_unknown(db, pid, "orphan", age_hours=24)
    _add_unknown(db, pid, "goal-scoped", goal_id=gid, age_hours=2)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    focus = _next_focus(conn, pid, {"id": gid, "subtasks": []})
    conn.close()
    assert "goal-scoped" in focus
    assert focus.startswith("Goal-linked unknown:")


def test_next_focus_falls_back_to_most_recent_project_unknown(tmp_path):
    """No goal-linked unknown → pick MOST RECENT project unknown (not oldest)."""
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _add_unknown(db, pid, "ancient", age_hours=240)
    _add_unknown(db, pid, "fresh", age_hours=2)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    focus = _next_focus(conn, pid, None)
    conn.close()
    assert "fresh" in focus
    assert "ancient" not in focus


def test_next_focus_returns_generic_when_no_unknowns(tmp_path):
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    focus = _next_focus(conn, pid, None)
    conn.close()
    assert "No specific focus" in focus


# ── build_situation: integration ────────────────────────────────────────


def test_build_situation_returns_dict_with_optional_keys(tmp_path):
    proj, pid = _build_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    _add_goal(db, pid, objective="open work", status="in_progress", age_hours=1)
    sit = build_situation(proj, pid)
    assert isinstance(sit, dict)
    assert "active_goal" in sit
    assert sit["active_goal"]["objective"] == "open work"
    assert "next_focus" in sit


def test_build_situation_with_no_project_id_returns_transaction_only(tmp_path):
    """None project_id: no DB queries, but transaction state still surfaced if any."""
    proj, _pid = _build_project(tmp_path)
    sit = build_situation(proj, None)
    assert isinstance(sit, dict)
    assert "active_goal" not in sit
    assert "project" not in sit
    # active_transaction MAY be present (depends on transaction file state) — both ok


def test_build_situation_empty_project_returns_empty_dict_gracefully(tmp_path):
    """No goals, no unknowns, no transaction → build_situation returns {} or near-empty."""
    proj, pid = _build_project(tmp_path)
    sit = build_situation(proj, pid)
    assert isinstance(sit, dict)
    # No goals → no active_goal key
    assert "active_goal" not in sit
    # next_focus always present (fallback string)
    assert "next_focus" in sit
    assert "No specific focus" in sit["next_focus"]


# ── HTTP path emits situation ───────────────────────────────────────────
#
# These two tests verify the proposal's acceptance criteria:
#   - GET /api/v1/bootstrap returns a top-level `situation` field
#   - The shape matches what CLI bootstrap returns
# We use the richer fixture from test_bootstrap_aggregator (full circles
# schema) and add the situation-specific extras (subtasks, description,
# scope columns on goals) so the whole payload composes cleanly.


def _build_full_project(tmp_path: Path, name: str = "sit-full") -> tuple[Path, str]:
    """Project with full circles schema + situation extras."""
    from tests.test_bootstrap_aggregator import _build_test_project

    proj, pid = _build_test_project(tmp_path, name)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        ALTER TABLE goals ADD COLUMN description TEXT;
        ALTER TABLE goals ADD COLUMN scope TEXT;
        CREATE TABLE subtasks (
            id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
            description TEXT, epistemic_importance REAL DEFAULT 0.5,
            status TEXT DEFAULT 'pending',
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, project_id TEXT, ai_id TEXT,
            created_timestamp REAL
        );
    """)
    conn.commit()
    conn.close()
    return proj, pid


def test_build_bootstrap_payload_includes_situation_key(tmp_path):
    """The daemon HTTP path now emits situation (was missing pre-fix)."""
    proj, pid = _build_full_project(tmp_path)
    db = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db))
    gid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO goals (id, project_id, objective, status, is_completed, "
        "goal_data, created_timestamp) VALUES (?,?,?,?,?,?,?)",
        (gid, pid, "hot work", "in_progress", 0, "{}", time.time() - 3600),
    )
    conn.commit()
    conn.close()

    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    assert "situation" in payload
    sit = payload["situation"]
    assert isinstance(sit, dict)
    assert sit.get("active_goal", {}).get("objective") == "hot work"


def test_build_bootstrap_payload_situation_is_first_key(tmp_path):
    """Per the proposal: situation surfaces first so attention-decay favors it."""
    proj, pid = _build_full_project(tmp_path)
    from empirica.core.bootstrap import build_bootstrap_payload

    payload = build_bootstrap_payload(project_path=proj, project_id=pid)
    assert next(iter(payload.keys())) == "situation"
