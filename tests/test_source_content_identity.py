"""Tests for migration 050 + content-identity computation at source-add.

Empirica slice of the unified source-identity model: matching local
sources to the central catalogue (reconcile) and body-sync-when-small
both key on content identity — content_hash + size_bytes + canonical_path
+ mime_type, computed best-effort at source-add for file-backed sources.

Coverage:
  1. migration_050 adds the four columns idempotently + the hash index
  2. fresh-DB CREATE TABLE schema includes the columns
  3. _compute_content_identity: file-backed → hash/size/path/mime;
     missing file → canonical_path only; no path → all None; never raises
  4. handler persists identity columns for a real file
  5. URL-only sources persist NULL identity (backward-compat envelope)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.artifact_log_commands import (
    _compute_content_identity,
)

# ── Migration ──────────────────────────────────────────────────────────

_IDENTITY_COLS = {"content_hash", "size_bytes", "canonical_path", "mime_type"}


def _columns(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(epistemic_sources)")
    return {row[1] for row in cursor.fetchall()}


def _pre_050_db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            discovered_at TIMESTAMP NOT NULL
        )
    """)
    return conn


def test_migration_050_adds_identity_columns(tmp_path):
    from empirica.data.migrations.migrations import (
        migration_050_source_content_identity,
    )

    conn = _pre_050_db(tmp_path)
    assert not (_IDENTITY_COLS & _columns(conn))
    migration_050_source_content_identity(conn.cursor())
    conn.commit()
    assert _columns(conn) >= _IDENTITY_COLS
    conn.close()


def test_migration_050_is_idempotent(tmp_path):
    from empirica.data.migrations.migrations import (
        migration_050_source_content_identity,
    )

    conn = _pre_050_db(tmp_path)
    migration_050_source_content_identity(conn.cursor())
    conn.commit()
    migration_050_source_content_identity(conn.cursor())
    conn.commit()
    cols = []
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(epistemic_sources)")
    cols = [row[1] for row in cursor.fetchall()]
    for col in _IDENTITY_COLS:
        assert cols.count(col) == 1
    conn.close()


def test_migration_050_creates_hash_index(tmp_path):
    from empirica.data.migrations.migrations import (
        migration_050_source_content_identity,
    )

    conn = _pre_050_db(tmp_path)
    migration_050_source_content_identity(conn.cursor())
    conn.commit()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_epistemic_sources_content_hash'"
    )
    assert cursor.fetchone() is not None
    conn.close()


# ── Fresh schema ───────────────────────────────────────────────────────


def test_fresh_schema_includes_identity_columns():
    from empirica.data.schema.projects_schema import SCHEMAS

    sources_ddl = next(
        (ddl for ddl in SCHEMAS if "epistemic_sources" in ddl), None
    )
    assert sources_ddl is not None
    for col in _IDENTITY_COLS:
        assert col in sources_ddl, f"{col} missing from fresh-DB DDL"


# ── _compute_content_identity ──────────────────────────────────────────


def test_identity_for_existing_file(tmp_path):
    f = tmp_path / "notes.md"
    body = b"# hello source identity\n"
    f.write_bytes(body)

    identity = _compute_content_identity(str(f))
    assert identity["canonical_path"] == str(f.resolve())
    assert identity["content_hash"] == f"sha256:{hashlib.sha256(body).hexdigest()}"
    assert identity["size_bytes"] == len(body)
    assert identity["mime_type"] == "text/markdown"


def test_identity_for_missing_file(tmp_path):
    """Path resolves canonically even when the file doesn't exist —
    hash/size/mime stay None (an honest pointer, not a failure)."""
    ghost = tmp_path / "moved-away.md"
    identity = _compute_content_identity(str(ghost))
    assert identity["canonical_path"] == str(ghost)
    assert identity["content_hash"] is None
    assert identity["size_bytes"] is None
    assert identity["mime_type"] is None


def test_identity_without_path():
    identity = _compute_content_identity(None)
    assert all(v is None for v in identity.values())


def test_identity_relative_path_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "rel.txt"
    f.write_bytes(b"x")
    identity = _compute_content_identity("rel.txt")
    assert identity["canonical_path"] == str(f.resolve())
    assert identity["size_bytes"] == 1


def test_identity_never_raises_on_unreadable(tmp_path):
    f = tmp_path / "dir-not-file"
    f.mkdir()
    identity = _compute_content_identity(str(f))
    # Directory: canonical_path set, content fields None
    assert identity["canonical_path"] == str(f.resolve())
    assert identity["content_hash"] is None


# ── Handler persistence ────────────────────────────────────────────────


def _make_args(**overrides) -> SimpleNamespace:
    defaults = {
        "title": "Identity test source",
        "description": None,
        "source_type": "document",
        "path": None,
        "url": None,
        "confidence": 0.7,
        "noetic": True,
        "praxic": False,
        "visibility": None,
        "session_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "output": "json",
        "verbose": False,
        "entity_type": None,
        "entity_id": None,
        "via": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    """Patch SessionDatabase so the handler writes to a tmp sqlite file
    carrying the post-050 schema."""
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            source_type TEXT NOT NULL,
            source_url TEXT,
            title TEXT NOT NULL,
            description TEXT,
            confidence REAL DEFAULT 0.5,
            epistemic_layer TEXT,
            discovered_by_ai TEXT,
            discovered_at TIMESTAMP NOT NULL,
            source_metadata TEXT,
            entity_type TEXT,
            entity_id TEXT,
            visibility TEXT DEFAULT 'shared',
            content_hash TEXT,
            size_bytes INTEGER,
            canonical_path TEXT,
            mime_type TEXT
        )
    """)
    conn.commit()
    conn.close()

    class _FakeDB:
        def __init__(self, *_a, **_kw):
            self.conn = sqlite3.connect(str(db_path))
            self.conn.row_factory = sqlite3.Row

        def close(self):
            self.conn.close()

    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase",
        _FakeDB,
        raising=False,
    )
    return db_path


def _run_handler(args, capsys):
    from empirica.cli.command_handlers.artifact_log_commands import (
        handle_source_add_command,
    )
    with patch(
        "empirica.cli.command_handlers.artifact_log_commands."
        "_source_persist_git_and_qdrant",
        return_value=(False, False),
    ):
        rc = handle_source_add_command(args)
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def _read_identity(db_path, source_id):
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT content_hash, size_bytes, canonical_path, mime_type "
        "FROM epistemic_sources WHERE id = ?",
        (source_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row


def test_handler_persists_identity_for_file(fake_db, tmp_path, capsys):
    f = tmp_path / "doc.md"
    body = b"persisted identity body\n"
    f.write_bytes(body)

    out = _run_handler(_make_args(path=str(f)), capsys)
    row = _read_identity(fake_db, out["source_id"])
    assert row[0] == f"sha256:{hashlib.sha256(body).hexdigest()}"
    assert row[1] == len(body)
    assert row[2] == str(f.resolve())
    assert row[3] == "text/markdown"


def test_handler_url_only_source_has_null_identity(fake_db, capsys):
    out = _run_handler(
        _make_args(url="https://example.com/spec.html"), capsys,
    )
    row = _read_identity(fake_db, out["source_id"])
    assert row == (None, None, None, None)
