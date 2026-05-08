"""Tests for get_file_relevant_artifacts (Item 4 — PreToolUse file-relevance).

Covers:
- Argument guarding: missing project / file → empty
- Missing DB → empty (no exception)
- Basename + relative-path needles match
- LIKE search hits across all 6 artifact tables
- Recency-ordered output, capped at limit
- project_id scoping
- format_relevance_nudge produces a clean one-liner from results
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from empirica.core.file_relevance import (
    _build_needles,
    _to_iso,
    format_relevance_nudge,
    get_file_relevant_artifacts,
)


@pytest.fixture
def project_db(tmp_path: Path) -> Path:
    """Bootstrap a project-shaped SQLite DB with the artifact tables we need."""
    db_dir = tmp_path / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))

    # Mimic the production schema — id, project_id, primary text col(s), timestamp
    schemas = {
        "project_findings": "id TEXT PRIMARY KEY, project_id TEXT, finding TEXT, created_timestamp REAL",
        "project_unknowns": "id TEXT PRIMARY KEY, project_id TEXT, unknown TEXT, created_timestamp REAL",
        "project_dead_ends": "id TEXT PRIMARY KEY, project_id TEXT, approach TEXT, why_failed TEXT, created_timestamp REAL",
        "mistakes_made": "id TEXT PRIMARY KEY, project_id TEXT, mistake TEXT, why_wrong TEXT, created_timestamp REAL",
        "assumptions": "id TEXT PRIMARY KEY, project_id TEXT, assumption TEXT, created_timestamp REAL",
        "decisions": "id TEXT PRIMARY KEY, project_id TEXT, choice TEXT, rationale TEXT, created_timestamp REAL",
    }
    for table, cols in schemas.items():
        conn.execute(f"CREATE TABLE {table} ({cols})")
    conn.commit()
    conn.close()
    return tmp_path


# ── Guard rails ────────────────────────────────────────────────────────


def test_returns_empty_for_blank_args(tmp_path: Path):
    assert get_file_relevant_artifacts("", "/some/file") == []
    assert get_file_relevant_artifacts(tmp_path, "") == []


def test_returns_empty_when_db_missing(tmp_path: Path):
    """Project root with no .empirica/sessions/sessions.db → empty."""
    assert get_file_relevant_artifacts(tmp_path, "/some/file.py") == []


def test_returns_empty_when_no_matches(project_db: Path):
    """DB exists with empty tables → empty."""
    assert get_file_relevant_artifacts(project_db, "auth.py") == []


# ── LIKE matching ──────────────────────────────────────────────────────


def test_matches_basename_in_finding(project_db: Path):
    fid = str(uuid.uuid4())
    conn = sqlite3.connect(project_db / ".empirica" / "sessions" / "sessions.db")
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding, created_timestamp) "
        "VALUES (?, ?, ?, ?)",
        (fid, "p1", "Stale comment in auth.py at line 42", time.time()),
    )
    conn.commit()
    conn.close()

    out = get_file_relevant_artifacts(project_db, "auth.py")
    assert len(out) == 1
    assert out[0]["id"] == fid
    assert out[0]["type"] == "finding"
    assert "auth.py" in out[0]["summary"]


def test_matches_relative_path_in_decision(project_db: Path):
    did = str(uuid.uuid4())
    conn = sqlite3.connect(project_db / ".empirica" / "sessions" / "sessions.db")
    conn.execute(
        "INSERT INTO decisions (id, project_id, choice, rationale, created_timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (did, "p1", "Use bcrypt", "src/auth/auth.py used MD5", time.time()),
    )
    conn.commit()
    conn.close()

    full_path = str(project_db / "src" / "auth" / "auth.py")
    out = get_file_relevant_artifacts(project_db, full_path)
    assert len(out) == 1
    assert out[0]["id"] == did
    assert out[0]["type"] == "decision"


def test_matches_secondary_text_column(project_db: Path):
    """why_failed (dead_ends) should also be searched."""
    deid = str(uuid.uuid4())
    conn = sqlite3.connect(project_db / ".empirica" / "sessions" / "sessions.db")
    conn.execute(
        "INSERT INTO project_dead_ends (id, project_id, approach, why_failed, created_timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (deid, "p1", "Tried passport.js", "Didn't fit auth.py middleware shape", time.time()),
    )
    conn.commit()
    conn.close()

    out = get_file_relevant_artifacts(project_db, "auth.py")
    assert len(out) == 1
    assert out[0]["id"] == deid


# ── Multi-table coverage ───────────────────────────────────────────────


def test_returns_hits_across_all_six_tables(project_db: Path):
    now = time.time()
    inserts = [
        ("project_findings", "INSERT INTO project_findings (id, project_id, finding, created_timestamp) VALUES (?, 'p1', 'finding about auth.py', ?)"),
        ("project_unknowns", "INSERT INTO project_unknowns (id, project_id, unknown, created_timestamp) VALUES (?, 'p1', 'unknown about auth.py', ?)"),
        ("project_dead_ends", "INSERT INTO project_dead_ends (id, project_id, approach, why_failed, created_timestamp) VALUES (?, 'p1', 'approach in auth.py', 'reason', ?)"),
        ("mistakes_made", "INSERT INTO mistakes_made (id, project_id, mistake, why_wrong, created_timestamp) VALUES (?, 'p1', 'mistake in auth.py', 'reason', ?)"),
        ("assumptions", "INSERT INTO assumptions (id, project_id, assumption, created_timestamp) VALUES (?, 'p1', 'assumption about auth.py', ?)"),
        ("decisions", "INSERT INTO decisions (id, project_id, choice, rationale, created_timestamp) VALUES (?, 'p1', 'choice for auth.py', 'rationale', ?)"),
    ]
    conn = sqlite3.connect(project_db / ".empirica" / "sessions" / "sessions.db")
    for _, sql in inserts:
        conn.execute(sql, (str(uuid.uuid4()), now))
    conn.commit()
    conn.close()

    out = get_file_relevant_artifacts(project_db, "auth.py", limit=20)
    types = {h["type"] for h in out}
    assert types == {"finding", "unknown", "dead_end", "mistake", "assumption", "decision"}


# ── Ordering + limits ─────────────────────────────────────────────────


def test_orders_by_recency_desc(project_db: Path):
    conn = sqlite3.connect(project_db / ".empirica" / "sessions" / "sessions.db")
    now = time.time()
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding, created_timestamp) VALUES (?, 'p1', 'older auth.py finding', ?)",
        (older_id, now - 10000),
    )
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding, created_timestamp) VALUES (?, 'p1', 'newer auth.py finding', ?)",
        (newer_id, now),
    )
    conn.commit()
    conn.close()

    out = get_file_relevant_artifacts(project_db, "auth.py")
    assert out[0]["id"] == newer_id
    assert out[1]["id"] == older_id


def test_respects_total_limit(project_db: Path):
    conn = sqlite3.connect(project_db / ".empirica" / "sessions" / "sessions.db")
    now = time.time()
    for i in range(10):
        conn.execute(
            "INSERT INTO project_findings (id, project_id, finding, created_timestamp) VALUES (?, 'p1', ?, ?)",
            (str(uuid.uuid4()), f"auth.py hit {i}", now - i),
        )
    conn.commit()
    conn.close()

    out = get_file_relevant_artifacts(project_db, "auth.py", limit=3)
    assert len(out) == 3


# ── project_id scoping ─────────────────────────────────────────────────


def test_project_id_scopes_search(project_db: Path):
    conn = sqlite3.connect(project_db / ".empirica" / "sessions" / "sessions.db")
    now = time.time()
    p1_id = str(uuid.uuid4())
    p2_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding, created_timestamp) VALUES (?, 'p1', 'auth.py thing', ?)",
        (p1_id, now),
    )
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding, created_timestamp) VALUES (?, 'p2', 'auth.py thing', ?)",
        (p2_id, now),
    )
    conn.commit()
    conn.close()

    p1_only = get_file_relevant_artifacts(project_db, "auth.py", project_id="p1")
    assert {h["id"] for h in p1_only} == {p1_id}

    both = get_file_relevant_artifacts(project_db, "auth.py")
    assert {h["id"] for h in both} == {p1_id, p2_id}


# ── needle building ───────────────────────────────────────────────────


def test_build_needles_returns_basename_only_for_outside_path(tmp_path: Path):
    needles = _build_needles(tmp_path, "/totally/different/auth.py")
    assert "auth.py" in needles
    # Outside-project paths shouldn't produce ".." rel paths
    for n in needles:
        assert not n.startswith("..")


def test_build_needles_returns_basename_and_rel(tmp_path: Path):
    target = tmp_path / "src" / "auth.py"
    needles = _build_needles(tmp_path, str(target))
    assert "auth.py" in needles
    assert any("src/auth.py" in n or "src\\auth.py" in n for n in needles)


def test_build_needles_skips_short_basenames(tmp_path: Path):
    """A 2-char basename would produce too many false positives."""
    needles = _build_needles(tmp_path, "/path/x")
    assert "x" not in needles


# ── Nudge formatting ──────────────────────────────────────────────────


def test_nudge_empty_for_no_artifacts():
    assert format_relevance_nudge([]) == ""


def test_nudge_summarizes_by_type():
    artifacts = [
        {"type": "finding", "id": "f1", "summary": "x"},
        {"type": "finding", "id": "f2", "summary": "x"},
        {"type": "decision", "id": "d1", "summary": "x"},
        {"type": "dead_end", "id": "de1", "summary": "x"},
    ]
    nudge = format_relevance_nudge(artifacts)
    assert nudge.startswith("FILE-RELEVANCE:")
    assert "2 findings" in nudge
    assert "1 decision" in nudge
    assert "1 dead-end" in nudge


def test_nudge_uses_singular_for_count_one():
    artifacts = [{"type": "finding", "id": "f1", "summary": "x"}]
    nudge = format_relevance_nudge(artifacts)
    assert "1 finding" in nudge
    assert "1 findings" not in nudge


# ── Timestamp normalization ──────────────────────────────────────────


def test_to_iso_handles_epoch_and_string_and_none():
    assert _to_iso(None) is None
    assert _to_iso("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    iso = _to_iso(time.time())
    assert iso is not None and iso.startswith("20")
