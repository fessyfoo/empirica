"""Tests for single-artifact CRUD endpoints (v0.5 LOCAL-ARTIFACTS T3).

GET /artifacts/{id}, PATCH /artifacts/{id}/resolve, PATCH /artifacts/{id},
DELETE /artifacts/{id}.

Polymorphic ID resolution across all 8 artifact tables. DELETE three-layer
cleanup (sqlite row + edges, Qdrant via _delete_from_qdrant, git notes via
_delete_artifact_git_notes — closes the documented delete-git-notes gap).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from empirica.api.serve_app import create_serve_app


def _make_project_with_db(tmp_path: Path, project_id: str) -> Path:
    """Reuse the same minimal-schema fixture pattern from T2 tests."""
    proj = tmp_path / f"proj-{project_id[:8]}"
    proj.mkdir()
    (proj / ".empirica").mkdir()
    (proj / ".empirica" / "project.yaml").write_text(
        f"name: test-project\nproject_id: {project_id}\n", encoding="utf-8"
    )
    db_dir = proj / ".empirica" / "sessions"
    db_dir.mkdir()
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            finding TEXT NOT NULL, finding_data TEXT,
            subject TEXT, impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            unknown TEXT NOT NULL, unknown_data TEXT,
            is_resolved INTEGER DEFAULT 0, resolved_by TEXT, resolved_timestamp REAL,
            impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE project_dead_ends (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            approach TEXT NOT NULL, why_failed TEXT, dead_end_data TEXT,
            impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE mistakes_made (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            mistake TEXT NOT NULL, why_wrong TEXT, prevention TEXT,
            mistake_data TEXT, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE assumptions (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            assumption TEXT NOT NULL, confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'unverified', resolution_finding_id TEXT,
            epistemic_source TEXT,
            created_timestamp REAL NOT NULL, resolved_timestamp REAL
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, transaction_id TEXT,
            choice TEXT NOT NULL, rationale TEXT, alternatives TEXT,
            confidence_at_decision REAL, reversibility TEXT,
            outcome TEXT, regret_score REAL, epistemic_source TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            source_type TEXT, source_url TEXT, title TEXT,
            description TEXT, confidence REAL DEFAULT 0.5,
            epistemic_layer TEXT, discovered_by_ai TEXT,
            discovered_at TIMESTAMP NOT NULL
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            transaction_id TEXT, objective TEXT NOT NULL,
            status TEXT DEFAULT 'in_progress', is_completed INTEGER DEFAULT 0,
            goal_data TEXT, created_timestamp REAL NOT NULL,
            completed_timestamp REAL
        );
        CREATE TABLE artifact_edges (
            from_id TEXT NOT NULL, to_id TEXT NOT NULL, relation TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            PRIMARY KEY (from_id, to_id, relation)
        );
    """)
    conn.commit()
    conn.close()
    return proj


def _insert_finding(db_path: Path, project_id: str, finding: str = "F", **kwargs) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, finding_data, "
        "impact, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess", finding, "{}", kwargs.get("impact", 0.5), time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_unknown(db_path: Path, project_id: str, unknown: str = "U") -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, unknown_data, "
        "is_resolved, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess", unknown, "{}", 0, time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_assumption(db_path: Path, project_id: str, assumption: str = "A", confidence: float = 0.5) -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO assumptions (id, project_id, session_id, assumption, confidence, status, "
        "created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess", assumption, confidence, "unverified", time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_goal(db_path: Path, project_id: str, objective: str = "G") -> str:
    art_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO goals (id, project_id, session_id, objective, status, is_completed, "
        "goal_data, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (art_id, project_id, "sess", objective, "in_progress", 0, "{}", time.time()),
    )
    conn.commit()
    conn.close()
    return art_id


def _insert_edge(db_path: Path, from_id: str, to_id: str, relation: str = "related"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
        (from_id, to_id, relation),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def reset_daemon_cache():
    import empirica.api.daemon_project as dp

    dp._cached = False
    dp._cached_project = None
    yield
    dp._cached = False
    dp._cached_project = None


# ── GET /artifacts/{id} ───────────────────────────────────────────────


def test_get_artifact_returns_finding_with_edges(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid, "main")
    u1 = _insert_unknown(db_path, pid)
    _insert_edge(db_path, f1, u1, "raises_question")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get(f"/api/v1/artifacts/{f1}")

    assert response.status_code == 200
    data = response.json()
    assert data["artifact"]["id"] == f1
    assert data["artifact"]["type"] == "finding"
    assert len(data["artifact"]["related_to"]) == 1
    assert data["artifact"]["related_to"][0]["id"] == u1


def test_get_artifact_404_when_id_unknown(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/artifacts/does-not-exist-id")

    assert response.status_code == 404


def test_get_artifact_polymorphic_across_types(tmp_path, monkeypatch, reset_daemon_cache):
    """ID resolution should find an artifact in any of the 8 tables."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    a1 = _insert_assumption(db_path, pid, "is X true?")
    g1 = _insert_goal(db_path, pid, "deliver X")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        a_resp = client.get(f"/api/v1/artifacts/{a1}").json()
        g_resp = client.get(f"/api/v1/artifacts/{g1}").json()

    assert a_resp["artifact"]["type"] == "assumption"
    assert g_resp["artifact"]["type"] == "goal"


# ── PATCH /artifacts/{id}/resolve ─────────────────────────────────────


def test_resolve_unknown_marks_resolved(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    u1 = _insert_unknown(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.patch(f"/api/v1/artifacts/{u1}/resolve", json={"resolved_by": "investigation"})

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "resolved"

    # Verify in DB
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT is_resolved, resolved_by FROM project_unknowns WHERE id = ?", (u1,)).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == "investigation"


def test_resolve_assumption_sets_verified(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    a1 = _insert_assumption(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.patch(f"/api/v1/artifacts/{a1}/resolve", json={"resolved_by": "code review"})

    assert response.status_code == 200
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT status FROM assumptions WHERE id = ?", (a1,)).fetchone()
    conn.close()
    assert row[0] == "verified"


def test_resolve_goal_completes(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    g1 = _insert_goal(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.patch(f"/api/v1/artifacts/{g1}/resolve", json={"resolved_by": "shipped"})

    assert response.status_code == 200
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT is_completed, status FROM goals WHERE id = ?", (g1,)).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == "completed"


def test_resolve_finding_returns_422_no_resolve_semantics(tmp_path, monkeypatch, reset_daemon_cache):
    """Findings have no 'resolve' state — should 422, not silently 200."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.patch(f"/api/v1/artifacts/{f1}/resolve", json={"resolved_by": "x"})

    assert response.status_code == 422


# ── PATCH /artifacts/{id} (partial update) ────────────────────────────


def test_patch_finding_updates_impact(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid, impact=0.4)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.patch(f"/api/v1/artifacts/{f1}", json={"impact": 0.85})

    assert response.status_code == 200
    assert response.json()["updated_fields"] == ["impact"]

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT impact FROM project_findings WHERE id = ?", (f1,)).fetchone()
    conn.close()
    assert row[0] == 0.85


def test_patch_drops_non_whitelisted_fields(tmp_path, monkeypatch, reset_daemon_cache):
    """Defensive: PATCH body fields outside the whitelist should be silently dropped.

    422 because after dropping, no whitelisted fields remain → empty update body.
    """
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.patch(
            f"/api/v1/artifacts/{f1}",
            json={"finding": "trying to rewrite the body!", "id": "new-id-attempt"},
        )

    assert response.status_code == 422
    # Confirm the row was untouched
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id, finding FROM project_findings WHERE id = ?", (f1,)).fetchone()
    conn.close()
    assert row[0] == f1
    # Body wasn't overwritten
    assert "trying to rewrite" not in row[1]


def test_patch_assumption_status_transitions(tmp_path, monkeypatch, reset_daemon_cache):
    """PATCH to set assumption.status = 'falsified' should work."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    a1 = _insert_assumption(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.patch(f"/api/v1/artifacts/{a1}", json={"status": "falsified"})

    assert response.status_code == 200
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT status FROM assumptions WHERE id = ?", (a1,)).fetchone()
    conn.close()
    assert row[0] == "falsified"


# ── DELETE /artifacts/{id} ─────────────────────────────────────────────


def test_delete_finding_removes_row(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.delete(f"/api/v1/artifacts/{f1}")

    assert response.status_code == 200
    assert response.json()["action"] == "deleted"

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM project_findings WHERE id = ?", (f1,)).fetchone()
    conn.close()
    assert row is None


def test_delete_cascades_dangling_edges(tmp_path, monkeypatch, reset_daemon_cache):
    """Deleting an artifact should also clean up edges where it's from_id OR to_id."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid, "to-delete")
    f2 = _insert_finding(db_path, pid, "kept")
    _insert_edge(db_path, f1, f2, "evidence")
    _insert_edge(db_path, f2, f1, "back-ref")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.delete(f"/api/v1/artifacts/{f1}")

    assert response.status_code == 200
    assert response.json()["edges_removed"] == 2

    conn = sqlite3.connect(str(db_path))
    edges = conn.execute("SELECT COUNT(*) FROM artifact_edges WHERE from_id = ? OR to_id = ?", (f1, f1)).fetchone()
    conn.close()
    assert edges[0] == 0


def test_delete_404_when_id_unknown(tmp_path, monkeypatch, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.delete("/api/v1/artifacts/does-not-exist")

    assert response.status_code == 404


def test_delete_subsequent_get_returns_404(tmp_path, monkeypatch, reset_daemon_cache):
    """Per spec test scenarios: 'subsequent GET 404s'."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    f1 = _insert_finding(db_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        client.delete(f"/api/v1/artifacts/{f1}")
        get_response = client.get(f"/api/v1/artifacts/{f1}")

    assert get_response.status_code == 404


# ── 503 contract ─────────────────────────────────────────────────────


def test_crud_endpoints_503_when_no_project(tmp_path, monkeypatch, reset_daemon_cache):
    bare = tmp_path / "outside"
    bare.mkdir()

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=None):
        monkeypatch.chdir(bare)
        monkeypatch.setenv("PWD", str(bare))
        client = TestClient(create_serve_app())
        get = client.get("/api/v1/artifacts/some-id")
        patch_resp = client.patch("/api/v1/artifacts/some-id", json={"impact": 0.5})
        resolve = client.patch("/api/v1/artifacts/some-id/resolve", json={"resolved_by": "x"})
        delete = client.delete("/api/v1/artifacts/some-id")

    for r in [get, patch_resp, resolve, delete]:
        assert r.status_code == 503
