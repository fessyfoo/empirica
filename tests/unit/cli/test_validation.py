"""Tests for empirica.cli.validation Pydantic models.

Verifies the work_type enum supports remote-ops (added 2026-04-08 as part
of the sentinel measurer remote-ops design).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from empirica.cli.validation import PreflightInput

# ---------------------------------------------------------------------------
# work_type regex — remote-ops support
# ---------------------------------------------------------------------------


def test_preflight_input_accepts_remote_ops_work_type():
    """work_type=remote-ops should validate successfully."""
    payload = {
        "session_id": "abc",
        "vectors": {"know": 0.5, "uncertainty": 0.5},
        "work_type": "remote-ops",
    }
    model = PreflightInput(**payload)
    assert model.work_type == "remote-ops"


def test_preflight_input_rejects_unknown_work_type():
    """An unknown work_type should still be rejected."""
    payload = {
        "session_id": "abc",
        "vectors": {"know": 0.5, "uncertainty": 0.5},
        "work_type": "garbage-not-a-real-type",
    }
    with pytest.raises(ValidationError):
        PreflightInput(**payload)


def test_preflight_input_accepts_existing_work_types():
    """Regression check: existing work_types should still validate."""
    for wt in ("code", "infra", "research", "release", "debug", "config", "docs", "data", "comms", "design", "audit"):
        payload = {
            "session_id": "abc",
            "vectors": {"know": 0.5, "uncertainty": 0.5},
            "work_type": wt,
        }
        model = PreflightInput(**payload)
        assert model.work_type == wt
