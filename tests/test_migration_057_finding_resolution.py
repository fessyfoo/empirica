"""Test migration 057 — resolution state on project_findings (#307).

Findings were the only artifact type with no resolve verb; this adds
is_resolved/resolution/resolved_timestamp/superseded_by so a stale or superseded
finding can be dropped from live retrieval while kept for history. Additive,
nullable, idempotent — mirrors project_unknowns.is_resolved.
"""

from __future__ import annotations

import sqlite3

from empirica.data.migrations.migrations import migration_057_finding_resolution


def _findings_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT NOT NULL)")
    return conn


def _cols(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(project_findings)").fetchall()}


def test_adds_resolution_columns():
    conn = _findings_db()
    migration_057_finding_resolution(conn.cursor())
    conn.commit()
    cols = _cols(conn)
    assert {"is_resolved", "resolution", "resolved_timestamp", "superseded_by"} <= cols


def test_columns_are_nullable():
    conn = _findings_db()
    migration_057_finding_resolution(conn.cursor())
    conn.commit()
    conn.execute("INSERT INTO project_findings (id, finding) VALUES ('f1', 'x')")
    row = conn.execute(
        "SELECT resolution, resolved_timestamp, superseded_by FROM project_findings WHERE id='f1'"
    ).fetchone()
    assert row == (None, None, None)


def test_creates_resolved_index():
    conn = _findings_db()
    migration_057_finding_resolution(conn.cursor())
    conn.commit()
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(project_findings)").fetchall()}
    assert "idx_project_findings_resolved" in indexes


def test_idempotent():
    conn = _findings_db()
    cur = conn.cursor()
    migration_057_finding_resolution(cur)
    migration_057_finding_resolution(cur)  # second run must not raise
    conn.commit()
    assert "is_resolved" in _cols(conn)
    conn.execute("INSERT INTO project_findings (id, finding, is_resolved, resolution) VALUES ('f2', 'y', 1, 'stale')")
    assert conn.execute("SELECT is_resolved FROM project_findings WHERE id='f2'").fetchone()[0] == 1
