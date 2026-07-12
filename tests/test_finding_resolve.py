"""Test the finding-resolve primitive (#307) at the repository layer.

``resolve_finding`` marks a finding resolved (kept for history, dropped from
live retrieval) — the resolve verb findings previously lacked. Mirrors
``resolve_unknown``. Prefix-matching lets a caller pass an 8+ char id fragment.
"""

from __future__ import annotations

import uuid

import pytest

from empirica.data.session_database import SessionDatabase

PROJECT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


@pytest.fixture
def fresh_db(tmp_path):
    db = SessionDatabase(db_path=str(tmp_path / "resolve.db"))
    yield db
    db.close()


def _row(db, fid):
    cur = db.conn.cursor()
    cur.execute(
        "SELECT is_resolved, resolution, resolved_timestamp, superseded_by FROM project_findings WHERE id = ?", (fid,)
    )
    return cur.fetchone()


def test_resolve_finding_flips_flag_and_stores_resolution(fresh_db):
    fid = fresh_db.log_finding(PROJECT_ID, SESSION_ID, "a stale finding")
    assert fresh_db.resolve_finding(fid, "stale") is True
    is_resolved, resolution, ts, superseded = _row(fresh_db, fid)
    assert is_resolved == 1
    assert resolution == "stale"
    assert ts is not None  # resolved_timestamp stamped
    assert superseded is None


def test_resolve_finding_records_superseded_by(fresh_db):
    old = fresh_db.log_finding(PROJECT_ID, SESSION_ID, "old belief")
    new = fresh_db.log_finding(PROJECT_ID, SESSION_ID, "corrected belief")
    assert fresh_db.resolve_finding(old, "superseded", superseded_by=new) is True
    assert _row(fresh_db, old)[3] == new  # superseded_by links to the replacement


def test_resolve_finding_prefix_match(fresh_db):
    fid = fresh_db.log_finding(PROJECT_ID, SESSION_ID, "prefix-resolvable finding")
    assert fresh_db.resolve_finding(fid[:8], "stale") is True  # 8-char prefix resolves
    assert _row(fresh_db, fid)[0] == 1


def test_resolve_finding_no_match_returns_false(fresh_db):
    assert fresh_db.resolve_finding("does-not-exist-00000000", "stale") is False


def test_resolve_finding_leaves_others_untouched(fresh_db):
    keep = fresh_db.log_finding(PROJECT_ID, SESSION_ID, "keep me live")
    drop = fresh_db.log_finding(PROJECT_ID, SESSION_ID, "resolve me")
    fresh_db.resolve_finding(drop, "stale")
    assert _row(fresh_db, keep)[0] in (0, None)  # untouched finding stays unresolved
    assert _row(fresh_db, drop)[0] == 1
