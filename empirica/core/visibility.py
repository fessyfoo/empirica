"""Visibility ladders — write-time enforcement against mesh sharing agreements.

When an artifact is logged with ``--visibility shared`` (or ``public``), the
intent declaration only earns reach if there is an active mesh sharing
agreement that ENABLES traffic at the required layer:

- ``shared`` → requires an active L2 agreement (same org, different tenant).
- ``public`` → requires an active L3 agreement (different org). Falls back
  to ``shared`` if only an L2 exists, then to ``local`` if neither.

This is the **write-time advisory check**. Cortex enforces authoritatively
on the consumer side (router, inbox/outbox poll); the local mirror may be
stale or empty (sync not yet bootstrapped). Per MESH_SHARING_AGREEMENTS.md
sync contract, we **fail-open** when the mirror has no agreements at all
(treat as 'mirror not yet bootstrapped', accept the intent) and only
downgrade when the mirror IS populated but lacks the required layer.

Same shape as the praxic-attempt-without-CHECK firewall: refuse the unsafe
transition, don't error.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


VISIBILITY_LOCAL = "local"
VISIBILITY_SHARED = "shared"
VISIBILITY_PUBLIC = "public"

# Layer-required mapping. shared needs at least L2; public needs L3.
_VISIBILITY_LAYER = {
    VISIBILITY_SHARED: "L2",
    VISIBILITY_PUBLIC: "L3",
}


def resolve_visibility_with_agreement(
    intended: str | None,
    *,
    repo=None,
) -> tuple[str, str | None]:
    """Return ``(resolved_visibility, warning_or_None)``.

    Args:
        intended: The CLI/API caller's declared visibility — ``'local'``,
            ``'shared'``, ``'public'``, or ``None`` (treat as default ``'local'``).
        repo: Optional ``WorkspaceDBRepository`` instance for the agreement
            mirror lookup. When ``None`` (test isolation, fail-soft path),
            opens via ``WorkspaceDBRepository.open()``.

    Returns:
        ``(resolved, warning)`` where ``resolved`` is the visibility the
        caller should actually use (possibly downgraded), and ``warning``
        is a human-readable string for stderr or ``None`` when no
        downgrade happened.

    Behaviour:

    - ``None`` / ``'local'`` → ``('local', None)``. No mirror lookup.
    - ``'shared'`` with active L2 agreement → ``('shared', None)``.
    - ``'shared'`` without active L2 agreement → ``('local', warning)``.
    - ``'public'`` with active L3 agreement → ``('public', None)``.
    - ``'public'`` with only L2 → ``('shared', warning)``.
    - ``'public'`` without either → ``('local', warning)``.

    Mirror **empty** (no agreements at all) → fail-open: keep intent,
    no warning. Rationale per spec: empty mirror most often means
    "sync not yet bootstrapped", and cortex enforces authoritatively
    on the consumer side anyway.
    """
    if not intended or intended == VISIBILITY_LOCAL:
        return VISIBILITY_LOCAL, None

    if intended not in _VISIBILITY_LAYER:
        # Unknown value — pass through, don't synthesise warnings.
        return intended, None

    try:
        has_l2, has_l3 = _scan_active_agreements(repo)
    except Exception as e:
        # Mirror unreadable → fail-open with debug log.
        logger.debug("visibility ladders: agreement scan failed (%s) — fail-open", e)
        return intended, None

    if has_l2 is None:
        # Sentinel value meaning "no agreements in mirror at all".
        # Could be an empty mirror or an un-bootstrapped install. Fail-open.
        return intended, None

    if intended == VISIBILITY_SHARED:
        if has_l2:
            return VISIBILITY_SHARED, None
        return VISIBILITY_LOCAL, _downgrade_warning(intended, VISIBILITY_LOCAL, "L2")

    # intended == 'public' — needs L3
    if has_l3:
        return VISIBILITY_PUBLIC, None
    if has_l2:
        return VISIBILITY_SHARED, _downgrade_warning(intended, VISIBILITY_SHARED, "L3")
    return VISIBILITY_LOCAL, _downgrade_warning(intended, VISIBILITY_LOCAL, "L3")


def _scan_active_agreements(repo=None) -> tuple[bool | None, bool]:
    """Return ``(has_l2_or_none, has_l3)`` from the workspace.db mirror.

    ``has_l2`` returns ``None`` (sentinel) when the mirror has NO mesh
    sharing agreements at all — the caller treats that as 'unbootstrapped'
    and fails-open. ``has_l3`` is always a bool.
    """
    import json as _json

    from empirica.core.mesh_sharing import ENTITY_TYPE
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    if repo is None:
        with WorkspaceDBRepository.open(ensure_schema=True) as opened:
            return _scan_active_agreements(repo=opened)

    rows = repo.list_entities(entity_type=ENTITY_TYPE, status="active", limit=1000)
    if not rows:
        # Also check the "all" status — a mirror with only revoked rows is still
        # a populated mirror (sync ran, agreements exist but inactive). Empty-all
        # is the unbootstrapped case.
        all_rows = repo.list_entities(entity_type=ENTITY_TYPE, status="all", limit=1)
        if not all_rows:
            return None, False
        return False, False

    has_l2 = False
    has_l3 = False
    for row in rows:
        try:
            meta = _json.loads(row.get("metadata") or "{}")
        except _json.JSONDecodeError:
            continue
        layer = meta.get("layer")
        if layer == "L2":
            has_l2 = True
        elif layer == "L3":
            has_l3 = True
        if has_l2 and has_l3:
            break
    return has_l2, has_l3


def _downgrade_warning(intended: str, resolved: str, required_layer: str) -> str:
    """Compose the stderr-friendly downgrade warning."""
    return (
        f"--visibility={intended} downgraded to {resolved}: "
        f"no active {required_layer} mesh sharing agreement in the local mirror. "
        f"Run 'empirica mesh-agreements sync' or wait for the next "
        f"<org>-mesh-sharing-changed ntfy event."
    )
