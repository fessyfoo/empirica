"""Tests for visibility ladders — write-time agreement check.

Surfaced by membrane SER ser_4272 substrate work (Tier B.3): when a CLI
caller declares --visibility shared/public, the intent only earns reach
if there is an active mesh sharing agreement at the required layer in
the local workspace.db mirror.

Coverage:
1. None / 'local' → ('local', None) — no mirror touch.
2. 'shared' with active L2 agreement in mirror → ('shared', None).
3. 'shared' with no L2 (but mirror is populated) → ('local', warning).
4. 'public' with active L3 → ('public', None).
5. 'public' with only L2 → ('shared', warning) — graceful step-down.
6. 'public' with no agreements at any layer in a populated mirror →
   ('local', warning).
7. Empty mirror (unbootstrapped) → fail-open: keep intent, no warning.
8. Mirror with only revoked agreements → treat as populated; downgrade.
9. Unknown visibility value → pass-through, no warning.
"""

from __future__ import annotations

import json
import time
from typing import Any


class _FakeRepo:
    """Minimal in-memory repo mirroring WorkspaceDBRepository.list_entities."""

    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []

    def list_entities(self, *, entity_type: str, status: str = 'active',
                      limit: int = 100) -> list[dict[str, Any]]:
        out = []
        for r in self._rows:
            if r['entity_type'] != entity_type:
                continue
            if status != 'all' and r.get('status') != status:
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out


def _agreement_row(*, agr_id: str, layer: str, status: str = 'active') -> dict[str, Any]:
    return {
        'entity_type': 'mesh_sharing_agreement',
        'entity_id': agr_id,
        'display_name': f'{agr_id} ({layer})',
        'description': None,
        'source_db': 'cortex',
        'source_table': 'mesh_sharing_agreements',
        'status': status,
        'created_at': time.time(),
        'updated_at': time.time(),
        'metadata': json.dumps({
            'layer': layer,
            'surfaces_json': ['all'],
            'direction': 'bidirectional',
        }),
    }


# --- Pass-through cases ---


def test_none_visibility_is_local_no_warning():
    from empirica.core.visibility import resolve_visibility_with_agreement
    resolved, warning = resolve_visibility_with_agreement(None, repo=_FakeRepo())
    assert resolved == 'local'
    assert warning is None


def test_local_visibility_is_local_no_warning():
    from empirica.core.visibility import resolve_visibility_with_agreement
    resolved, warning = resolve_visibility_with_agreement('local', repo=_FakeRepo())
    assert resolved == 'local'
    assert warning is None


def test_unknown_visibility_passes_through():
    """Defensive: an unknown string (e.g. typo'd 'sharedd') passes through.
    The artifact log validates against argparse choices first; this is a
    backstop for the programmatic API."""
    from empirica.core.visibility import resolve_visibility_with_agreement
    resolved, warning = resolve_visibility_with_agreement('weird', repo=_FakeRepo())
    assert resolved == 'weird'
    assert warning is None


# --- shared paths ---


def test_shared_with_active_L2_no_downgrade():
    from empirica.core.visibility import resolve_visibility_with_agreement
    repo = _FakeRepo([_agreement_row(agr_id='agr_l2', layer='L2')])
    resolved, warning = resolve_visibility_with_agreement('shared', repo=repo)
    assert resolved == 'shared'
    assert warning is None


def test_shared_without_L2_downgrades_to_local():
    """Populated mirror with only L3 agreements — 'shared' wants L2, has none → local."""
    from empirica.core.visibility import resolve_visibility_with_agreement
    repo = _FakeRepo([_agreement_row(agr_id='agr_l3', layer='L3')])
    resolved, warning = resolve_visibility_with_agreement('shared', repo=repo)
    assert resolved == 'local'
    assert warning is not None
    assert '--visibility=shared' in warning
    assert 'L2' in warning


# --- public paths ---


def test_public_with_active_L3_no_downgrade():
    from empirica.core.visibility import resolve_visibility_with_agreement
    repo = _FakeRepo([_agreement_row(agr_id='agr_l3', layer='L3')])
    resolved, warning = resolve_visibility_with_agreement('public', repo=repo)
    assert resolved == 'public'
    assert warning is None


def test_public_with_only_L2_downgrades_to_shared():
    """Public wants L3 but only L2 exists — graceful step-down to shared, warning."""
    from empirica.core.visibility import resolve_visibility_with_agreement
    repo = _FakeRepo([_agreement_row(agr_id='agr_l2', layer='L2')])
    resolved, warning = resolve_visibility_with_agreement('public', repo=repo)
    assert resolved == 'shared'
    assert warning is not None
    assert '--visibility=public' in warning
    assert 'L3' in warning


def test_public_with_no_layer_match_downgrades_to_local():
    """Populated mirror but no L2 OR L3 layer matches — full step-down to local."""
    from empirica.core.visibility import resolve_visibility_with_agreement
    repo = _FakeRepo([_agreement_row(agr_id='agr_l1', layer='L1')])
    resolved, warning = resolve_visibility_with_agreement('public', repo=repo)
    assert resolved == 'local'
    assert warning is not None
    assert 'L3' in warning


# --- Empty / revoked mirror ---


def test_empty_mirror_fails_open_for_shared():
    """No agreements at all in mirror → treat as 'unbootstrapped', keep
    intent + no warning. Cortex enforces authoritatively on consumer side."""
    from empirica.core.visibility import resolve_visibility_with_agreement
    resolved, warning = resolve_visibility_with_agreement('shared', repo=_FakeRepo())
    assert resolved == 'shared'
    assert warning is None


def test_empty_mirror_fails_open_for_public():
    from empirica.core.visibility import resolve_visibility_with_agreement
    resolved, warning = resolve_visibility_with_agreement('public', repo=_FakeRepo())
    assert resolved == 'public'
    assert warning is None


def test_mirror_with_only_revoked_downgrades():
    """Mirror IS populated (sync ran) but only revoked rows — that's a real
    "no active agreement" state, not unbootstrapped. Downgrade."""
    from empirica.core.visibility import resolve_visibility_with_agreement
    repo = _FakeRepo([
        _agreement_row(agr_id='agr_old', layer='L2', status='revoked'),
    ])
    resolved, warning = resolve_visibility_with_agreement('shared', repo=repo)
    assert resolved == 'local'
    assert warning is not None


# --- Error path ---


class _ExplodingRepo:
    def list_entities(self, **_kwargs):
        raise OSError("simulated mirror unreachable")


def test_mirror_error_fails_open():
    """If the mirror lookup throws (DB locked, schema mismatch, etc.),
    fail-open with debug log — don't break the caller's write path."""
    from empirica.core.visibility import resolve_visibility_with_agreement
    resolved, warning = resolve_visibility_with_agreement(
        'shared', repo=_ExplodingRepo(),
    )
    assert resolved == 'shared'
    assert warning is None
