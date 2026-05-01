"""Visibility tiers for epistemic artifacts (Phase 0 of PROPOSAL_VISIBILITY_TIERS.md).

Three tiers govern where artifacts can travel:

- ``public``  — publicly shareable; safe to push to a public repo.
- ``shared``  — team-private, co-versioned (the default). Phase 1 will
  encrypt these via git-crypt; Phase 0 is metadata-only.
- ``local``   — machine-local, never shared (raw secrets, session state).

Phase 0 stores the tier in a dedicated ``visibility`` column on each artifact
table. Validation lives here in Python because SQLite ALTER TABLE ADD COLUMN
does not support CHECK constraints on existing rows; the CLI and repository
layer normalize input through :func:`normalize_visibility` before persisting.
"""

from typing import Literal

VisibilityTier = Literal['public', 'shared', 'local']

VISIBILITY_TIERS: tuple[str, ...] = ('public', 'shared', 'local')

DEFAULT_VISIBILITY: str = 'shared'


def normalize_visibility(value: str | None) -> str:
    """Return a valid tier or fall back to the default.

    ``None`` and unknown values both resolve to ``DEFAULT_VISIBILITY`` ('shared') —
    the safest invariant: never accidentally promote an artifact to ``public``.
    """
    if value is None:
        return DEFAULT_VISIBILITY
    v = str(value).strip().lower()
    if v in VISIBILITY_TIERS:
        return v
    return DEFAULT_VISIBILITY
