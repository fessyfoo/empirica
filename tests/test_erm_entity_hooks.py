"""Tests for ERM §6.2 write hooks + backfill verb (PR-b).

The mint paths fire a best-effort embed hook after the registry write; the
`entity-reindex` verb backfills every row. Qdrant is mocked throughout — these
pin the WIRING (hook fires with the right args, backfill walks all rows, dry-run
+ type filter), not the embedding itself (covered in test_workspace_index_entity).
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import empirica.cli.command_handlers.entity_commands as ec
from empirica.core.qdrant import workspace_index as wi
from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _ensure_workspace_schema


@pytest.fixture()
def mem_repo():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    return WorkspaceDBRepository(conn)


def _embed_mock():
    return patch.object(wi, "embed_entity_to_workspace_index", MagicMock(return_value=True))


class _RepoCM:
    """Context-manager wrapper so a shared in-memory repo backs .open()."""

    def __init__(self, repo):
        self.repo = repo

    def __enter__(self):
        return self.repo

    def __exit__(self, *_a):
        return False


# ── writer text tolerance (the real contact metadata key) ─────────────────────


def test_compose_contact_tolerates_company_name():
    t = wi._compose_entity_text("contact", "Carly", "", {"company_name": "Empirica Foundation", "role": "Admiral"})
    assert "Empirica Foundation" in t and "Admiral" in t


# ── best-effort hook helper ───────────────────────────────────────────────────


def test_embed_entity_row_swallows_errors():
    # A Qdrant hiccup must never propagate out of the mint path.
    with patch.object(wi, "embed_entity_to_workspace_index", side_effect=RuntimeError("qdrant down")):
        ec._embed_entity_row("contact", "c-1", "X")  # must not raise


# ── mint hooks fire the embed ─────────────────────────────────────────────────


def test_mint_contact_hooks_embed(mem_repo):
    with _embed_mock() as m:
        ec.mint_contact(name="Carly", email="carly@x.com", company_name="Foundation", repo=mem_repo)
    assert m.called
    kw = m.call_args.kwargs
    assert kw["entity_type"] == "contact" and kw["display_name"] == "Carly"


def test_mint_entity_hooks_embed(mem_repo):
    with _embed_mock() as m:
        ec.mint_entity(entity_type="organization", name="NLE", repo=mem_repo)
    assert m.called
    assert m.call_args.kwargs["entity_type"] == "organization"


# ── entity-reindex backfill ───────────────────────────────────────────────────


def _seed(repo, *types):
    for i, et in enumerate(types):
        repo.upsert_entity(
            entity_type=et,
            entity_id=f"{et}-{i}",
            display_name=f"Name{i}",
            source_db="workspace",
            source_table=et,
            metadata="{}",
        )


def _run_reindex(repo, **args):
    opts = {"output": "json", "dry_run": False, "type": None, "verbose": False, **args}
    ns = SimpleNamespace(**opts)
    with (
        patch.object(WorkspaceDBRepository, "open", return_value=_RepoCM(repo)),
        _embed_mock() as m,
        pytest.raises(SystemExit) as ex,
    ):
        ec.handle_entity_reindex_command(ns)
    return m, ex.value.code


def test_reindex_embeds_all_rows(mem_repo):
    _seed(mem_repo, "contact", "organization", "engagement")
    m, code = _run_reindex(mem_repo)
    assert code == 0
    assert m.call_count == 3


def test_reindex_dry_run_counts_without_embedding(mem_repo):
    _seed(mem_repo, "contact", "organization")
    m, code = _run_reindex(mem_repo, dry_run=True)
    assert code == 0
    assert m.call_count == 0  # dry-run never touches Qdrant


def test_reindex_type_filter_scopes(mem_repo):
    _seed(mem_repo, "contact", "organization", "engagement")
    m, code = _run_reindex(mem_repo, type="contact")
    assert code == 0
    assert m.call_count == 1
    assert m.call_args.kwargs["entity_type"] == "contact"
