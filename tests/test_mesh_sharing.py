"""Tests for the mesh_sharing_agreement entity_registry mirror.

Covers:
- Layer derivation (L1/L2/L3) from party pairs
- MeshSharingAgreement.from_cortex_row round-trips
- sync_from_cortex upserts new agreements, updates existing, marks-revoked removals
- sync_from_cortex tolerates fetch failure (returns SyncResult.error, mirror unchanged)
- is_agreement_active honors party-pair match, surface filter, L1 bypass

See docs/architecture/MESH_SHARING_AGREEMENTS.md for the design.
"""

from __future__ import annotations

import json
from typing import Any

from empirica.core.mesh_sharing import (
    ENTITY_TYPE,
    LAYER_L1,
    LAYER_L2,
    LAYER_L3,
    MeshSharingAgreement,
    SyncResult,
    derive_layer,
    is_agreement_active,
    sync_from_cortex,
)

# --- derive_layer ---


def test_derive_layer_l1_same_org_same_tenant():
    assert derive_layer('empirica', 'david', 'empirica', 'david') == LAYER_L1


def test_derive_layer_l2_same_org_diff_tenant():
    assert derive_layer('empirica', 'david', 'empirica', 'philipp') == LAYER_L2


def test_derive_layer_l3_diff_org():
    assert derive_layer('empirica', 'david', 'external', 'admin') == LAYER_L3


def test_derive_layer_l2_org_wide_party():
    """tenant=None on one side = org-wide. Still L2 if other has a specific tenant."""
    assert derive_layer('empirica', None, 'empirica', 'david') == LAYER_L2


# --- MeshSharingAgreement.from_cortex_row ---


def test_from_cortex_row_basic_l2():
    row = {
        'id': 'agr_abc',
        'party_a_org': 'empirica', 'party_a_tenant': 'david',
        'party_b_org': 'empirica', 'party_b_tenant': 'philipp',
        'state': 'active',
        'surfaces': ['collab', 'eco'],
        'direction': 'bidirectional',
        'eco_always': False,
        'terms': {'description': 'cross-tenant david<->philipp share'},
        'created_at': 1700000000.0,
        'created_by_admin': 'admin_uuid',
        'last_transition_at': 1700000100.0,
        'last_transition_actor': 'admin_uuid',
        'expires_at': None,
    }
    agr = MeshSharingAgreement.from_cortex_row(row)
    assert agr.id == 'agr_abc'
    assert agr.layer == LAYER_L2
    assert agr.surfaces == ['collab', 'eco']
    assert agr.eco_always is False
    assert agr.display_name == 'empirica.david ↔ empirica.philipp'


def test_from_cortex_row_l3_forces_eco_always_default():
    """L3 (diff org) defaults eco_always=True when row doesn't specify."""
    row = {
        'id': 'agr_xyz',
        'party_a_org': 'empirica', 'party_a_tenant': 'david',
        'party_b_org': 'external', 'party_b_tenant': None,
        'state': 'active',
    }
    agr = MeshSharingAgreement.from_cortex_row(row)
    assert agr.layer == LAYER_L3
    assert agr.eco_always is True  # derived from layer when not specified


def test_from_cortex_row_handles_surfaces_as_json_string():
    """Cortex may send surfaces_json as a JSON-encoded string."""
    row = {
        'id': 'agr_str',
        'party_a_org': 'a', 'party_a_tenant': None,
        'party_b_org': 'b', 'party_b_tenant': None,
        'state': 'active',
        'surfaces_json': '["collab"]',
    }
    agr = MeshSharingAgreement.from_cortex_row(row)
    assert agr.surfaces == ['collab']


def test_from_cortex_row_minimal_shape_tenant_tenant():
    """Cortex's current live shape: party_a/party_b strings + scope. L2."""
    row = {
        'id': 'agr_min',
        'party_a': 'user_a_uuid',
        'party_b': 'user_b_uuid',
        'scope': 'tenant_tenant',
        'state': 'active',
        'activated_at': 1700000100.0,
        'initiator_user_id': 'user_a_uuid',
        'created_at': 1700000000.0,
    }
    agr = MeshSharingAgreement.from_cortex_row(row)
    assert agr.id == 'agr_min'
    assert agr.layer == LAYER_L2
    assert agr.party_a_tenant == 'user_a_uuid'
    assert agr.party_b_tenant == 'user_b_uuid'
    assert agr.surfaces == ['collab']  # default
    assert agr.last_transition_at == 1700000100.0  # from activated_at
    assert agr.created_by_admin == 'user_a_uuid'  # from initiator_user_id


def test_from_cortex_row_minimal_shape_org_org_is_l3():
    """scope='org_org' on minimal shape → L3."""
    row = {
        'id': 'agr_orgorg',
        'party_a': 'org_a_id',
        'party_b': 'org_b_id',
        'scope': 'org_org',
        'state': 'active',
    }
    agr = MeshSharingAgreement.from_cortex_row(row)
    assert agr.layer == LAYER_L3
    assert agr.party_a_tenant is None  # org-wide
    assert agr.party_b_tenant is None
    assert agr.eco_always is True  # L3 default


def test_to_metadata_json_roundtrip():
    row = {
        'id': 'agr_rt',
        'party_a_org': 'empirica', 'party_a_tenant': 'david',
        'party_b_org': 'empirica', 'party_b_tenant': 'philipp',
        'state': 'active',
        'surfaces': ['collab'],
        'direction': 'a_to_b',
        'eco_always': True,
        'created_at': 1700000000.0,
    }
    agr = MeshSharingAgreement.from_cortex_row(row)
    meta = json.loads(agr.to_metadata_json())
    assert meta['party_a_tenant'] == 'david'
    assert meta['layer'] == LAYER_L2
    assert meta['surfaces_json'] == ['collab']
    assert meta['eco_always'] is True


# --- FakeRepo for sync tests ---


class FakeRepo:
    """In-memory stand-in for WorkspaceDBRepository, exposes the three
    methods sync_from_cortex needs."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def upsert_entity(
        self, entity_type, entity_id, display_name, source_db, source_table,
        description=None, emoji_state=None, status='active', metadata=None,
    ):
        self.rows[(entity_type, entity_id)] = {
            'entity_type': entity_type,
            'entity_id': entity_id,
            'display_name': display_name,
            'description': description,
            'source_db': source_db,
            'source_table': source_table,
            'emoji_state': emoji_state,
            'status': status,
            'metadata': metadata,
        }

    def get_entity(self, entity_type, entity_id):
        return self.rows.get((entity_type, entity_id))

    def list_entities(self, entity_type=None, status='active', limit=100):
        out = []
        for (et, _eid), row in self.rows.items():
            if entity_type and et != entity_type:
                continue
            if status != 'all' and row['status'] != status:
                continue
            out.append(row)
        return out[:limit]

    def mark_entity_status(self, entity_type, entity_id, status):
        row = self.rows.get((entity_type, entity_id))
        if row is None:
            return False
        row['status'] = status
        return True


# --- sync_from_cortex ---


def test_sync_upserts_new_agreement():
    repo = FakeRepo()
    fake_rows = [{
        'id': 'agr_new',
        'party_a_org': 'empirica', 'party_a_tenant': 'david',
        'party_b_org': 'empirica', 'party_b_tenant': 'philipp',
        'state': 'active',
        'surfaces': ['collab'],
        'direction': 'bidirectional',
    }]
    result = sync_from_cortex(repo, 'http://cortex', 'key',
                              fetcher=lambda u, k: fake_rows)
    assert result.error is None
    assert result.added == 1
    assert result.updated == 0
    assert result.marked_revoked == 0
    row = repo.get_entity(ENTITY_TYPE, 'agr_new')
    assert row is not None
    assert row['status'] == 'active'


def test_sync_updates_existing_agreement():
    repo = FakeRepo()
    # First sync — adds
    fake_rows = [{
        'id': 'agr_upd', 'party_a_org': 'a', 'party_a_tenant': None,
        'party_b_org': 'b', 'party_b_tenant': None, 'state': 'proposed',
    }]
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: fake_rows)

    # Second sync — same id, different state → updates
    fake_rows[0]['state'] = 'active'
    result = sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: fake_rows)
    assert result.added == 0
    assert result.updated == 1
    assert result.marked_revoked == 0
    assert repo.get_entity(ENTITY_TYPE, 'agr_upd')['status'] == 'active'


def test_sync_marks_revoked_when_removed_from_cortex():
    repo = FakeRepo()
    # Seed: one agreement in mirror
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr_gone', 'party_a_org': 'a', 'party_a_tenant': None,
         'party_b_org': 'b', 'party_b_tenant': None, 'state': 'active'},
    ])
    assert repo.get_entity(ENTITY_TYPE, 'agr_gone')['status'] == 'active'

    # Next sync returns empty → mark-revoked sweep fires
    result = sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [])
    assert result.marked_revoked == 1
    assert repo.get_entity(ENTITY_TYPE, 'agr_gone')['status'] == 'revoked'


def test_sync_revoked_idempotent():
    """Re-running mark-revoked on already-revoked rows does nothing."""
    repo = FakeRepo()
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr_done', 'party_a_org': 'a', 'party_a_tenant': None,
         'party_b_org': 'b', 'party_b_tenant': None, 'state': 'active'},
    ])
    # First empty sync → marked revoked
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [])
    # Second empty sync → no-op (already revoked)
    result = sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [])
    assert result.marked_revoked == 0


def test_sync_fetch_failure_leaves_mirror_unchanged():
    """Fetcher raising → SyncResult.error set, no mirror writes."""
    repo = FakeRepo()
    # Seed mirror with an existing row
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr_keep', 'party_a_org': 'a', 'party_a_tenant': None,
         'party_b_org': 'b', 'party_b_tenant': None, 'state': 'active'},
    ])

    def boom(url, key):
        import urllib.error
        raise urllib.error.URLError("network down")

    result = sync_from_cortex(repo, 'http://cortex', 'key', fetcher=boom)
    assert result.error is not None
    assert 'fetch failed' in result.error
    # Mirror unchanged
    assert repo.get_entity(ENTITY_TYPE, 'agr_keep')['status'] == 'active'


def test_sync_skips_malformed_rows():
    repo = FakeRepo()
    fake_rows = [
        # Valid
        {'id': 'agr_ok', 'party_a_org': 'a', 'party_a_tenant': None,
         'party_b_org': 'b', 'party_b_tenant': None, 'state': 'active'},
        # Missing party_a_org — should be skipped, not crash
        {'id': 'agr_broken', 'party_b_org': 'b', 'state': 'active'},
    ]
    result = sync_from_cortex(repo, 'http://cortex', 'key',
                              fetcher=lambda u, k: fake_rows)
    assert result.added == 1
    assert repo.get_entity(ENTITY_TYPE, 'agr_ok') is not None
    assert repo.get_entity(ENTITY_TYPE, 'agr_broken') is None


# --- is_agreement_active ---


def test_l1_bypasses_agreement_check():
    """Same org + same tenant always returns True — no agreement needed."""
    repo = FakeRepo()  # empty mirror
    assert is_agreement_active(repo, 'empirica', 'david', 'empirica', 'david') is True


def test_no_active_l2_agreement_returns_false():
    repo = FakeRepo()
    assert is_agreement_active(repo, 'empirica', 'david', 'empirica', 'philipp') is False


def test_active_l2_agreement_returns_true():
    repo = FakeRepo()
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr1', 'party_a_org': 'empirica', 'party_a_tenant': 'david',
         'party_b_org': 'empirica', 'party_b_tenant': 'philipp', 'state': 'active'},
    ])
    assert is_agreement_active(repo, 'empirica', 'david', 'empirica', 'philipp') is True


def test_agreement_direction_agnostic():
    """A->B agreement also matches B->A queries (bilateral lookup)."""
    repo = FakeRepo()
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr_d', 'party_a_org': 'empirica', 'party_a_tenant': 'david',
         'party_b_org': 'empirica', 'party_b_tenant': 'philipp', 'state': 'active'},
    ])
    # Query in reverse order
    assert is_agreement_active(repo, 'empirica', 'philipp', 'empirica', 'david') is True


def test_proposed_agreement_does_not_satisfy_active_check():
    repo = FakeRepo()
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr_p', 'party_a_org': 'a', 'party_a_tenant': None,
         'party_b_org': 'b', 'party_b_tenant': None, 'state': 'proposed'},
    ])
    assert is_agreement_active(repo, 'a', None, 'b', None) is False


def test_surface_filter_matches_specific_surface():
    repo = FakeRepo()
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr_collab_only', 'party_a_org': 'a', 'party_a_tenant': None,
         'party_b_org': 'b', 'party_b_tenant': None, 'state': 'active',
         'surfaces': ['collab']},
    ])
    assert is_agreement_active(repo, 'a', None, 'b', None, surface='collab') is True
    assert is_agreement_active(repo, 'a', None, 'b', None, surface='eco') is False


def test_surface_all_matches_any():
    repo = FakeRepo()
    sync_from_cortex(repo, 'http://cortex', 'key', fetcher=lambda u, k: [
        {'id': 'agr_all', 'party_a_org': 'a', 'party_a_tenant': None,
         'party_b_org': 'b', 'party_b_tenant': None, 'state': 'active',
         'surfaces': ['all']},
    ])
    assert is_agreement_active(repo, 'a', None, 'b', None, surface='collab') is True
    assert is_agreement_active(repo, 'a', None, 'b', None, surface='eco') is True


def test_sync_result_summary_line():
    r = SyncResult(added=3, updated=1, marked_revoked=2)
    assert 'added' in r.summary_line() and 'updated' in r.summary_line()
    r_err = SyncResult(error='network')
    assert 'failed' in r_err.summary_line() and 'network' in r_err.summary_line()
