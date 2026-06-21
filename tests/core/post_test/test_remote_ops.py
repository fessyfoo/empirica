"""Tests for remote-ops work_type — the on/off switch for ungroundable work.

remote-ops marks transactions where the local Sentinel has no signal for
the work being done (SSH sessions, customer machines, remote config, etc).
The relevance entry zeros every source, which routes every vector with
evidence into insufficient_evidence_vectors. The AI's self-assessment
stands unchallenged.

These tests verify the architectural property end-to-end through the mapper
without needing to spin up a real session DB or run the full collector chain.
"""

from __future__ import annotations

from empirica.core.post_test.collector import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
)
from empirica.core.post_test.mapper import EvidenceMapper

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle_with_evidence(session_id: str = "rops-test") -> EvidenceBundle:
    """Build a bundle with evidence touching multiple vectors from multiple
    sources. Used to verify remote-ops zeros them all out."""
    bundle = EvidenceBundle(session_id=session_id)
    bundle.items = [
        EvidenceItem(
            source="artifacts",
            metric_name="findings_logged",
            value=0.7,
            raw_value={"count": 5},
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=["know", "signal"],
        ),
        EvidenceItem(
            source="noetic",
            metric_name="investigation_depth",
            value=0.6,
            raw_value={"depth": 3},
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=["context", "do"],
        ),
        EvidenceItem(
            source="git",
            metric_name="lines_changed",
            value=0.5,
            raw_value=100,
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=["change", "state"],
        ),
        EvidenceItem(
            source="goals",
            metric_name="completion_ratio",
            value=0.8,
            raw_value=0.8,
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=["completion"],
        ),
    ]
    bundle.sources_available = ["artifacts", "noetic", "git", "goals"]
    return bundle


# ---------------------------------------------------------------------------
# remote-ops mapper behavior
# ---------------------------------------------------------------------------


def test_remote_ops_excludes_all_evidence_sources():
    """work_type=remote-ops should leave grounded dict empty and flag every
    seen vector as insufficient_evidence."""
    bundle = _make_bundle_with_evidence()
    self_assessed = {
        "know": 0.8,
        "signal": 0.7,
        "context": 0.6,
        "do": 0.7,
        "change": 0.5,
        "state": 0.6,
        "completion": 0.9,
        "uncertainty": 0.3,
    }

    mapper = EvidenceMapper()
    assessment = mapper.map_evidence(
        bundle,
        self_assessed,
        phase="combined",
        domain="default",
        work_type="remote-ops",
    )

    # No grounded values — every source was excluded.
    assert assessment.grounded == {}, (
        "remote-ops should produce no grounded estimates: every relevance weight is 0.0 so no source contributes"
    )

    # No calibration gaps — there's nothing to compare against.
    assert assessment.calibration_gaps == {}, "remote-ops should produce no calibration gaps"

    # Every vector that had evidence lands in insufficient_evidence_vectors.
    expected_in_insufficient = {
        "know",
        "signal",
        "context",
        "do",
        "change",
        "state",
        "completion",
    }
    assert expected_in_insufficient <= set(assessment.insufficient_evidence_vectors), (
        f"Expected all vectors with evidence to be insufficient, got {assessment.insufficient_evidence_vectors}"
    )


def test_remote_ops_self_assessed_vectors_unchanged():
    """The self_assessed dict on the assessment matches what was passed in —
    remote-ops doesn't touch it."""
    bundle = _make_bundle_with_evidence()
    self_assessed = {"know": 0.8, "uncertainty": 0.2}
    mapper = EvidenceMapper()
    assessment = mapper.map_evidence(
        bundle,
        self_assessed,
        phase="combined",
        work_type="remote-ops",
    )
    assert assessment.self_assessed == self_assessed


def test_remote_ops_zero_calibration_score():
    """With no grounded vectors, the overall calibration score should
    naturally be 0 (no divergences to weight)."""
    bundle = _make_bundle_with_evidence()
    self_assessed = {"know": 0.8, "uncertainty": 0.2}
    mapper = EvidenceMapper()
    assessment = mapper.map_evidence(
        bundle,
        self_assessed,
        phase="combined",
        work_type="remote-ops",
    )
    # No gaps means no weighted score contribution
    assert assessment.overall_calibration_score == 0.0


# ---------------------------------------------------------------------------
# Task 16: End-to-end integration through run_grounded_verification
# ---------------------------------------------------------------------------


def _make_test_db(tmp_path):
    """Build a temporary SessionDatabase for integration tests that need
    the wrapper's calibration_insights + storage operations to run."""
    from empirica.data.session_database import SessionDatabase

    return SessionDatabase(str(tmp_path / "test.db"))


def test_run_grounded_verification_remote_ops_combined_mode(tmp_path):
    """End-to-end: run_grounded_verification with work_type=remote-ops
    in combined mode (no phase_boundary) returns an ungrounded response.
    The short-circuit fires inside _run_single_phase_verification, but
    the outer wrapper still needs a real DB for its calibration_insights
    analyzer and store_verification calls (which the short-circuit skips,
    but the wrapper's other code paths touch DB anyway)."""
    from empirica.core.post_test.grounded_calibration import (
        run_grounded_verification,
    )

    db = _make_test_db(tmp_path)

    result = run_grounded_verification(
        session_id="rops-e2e-combined",
        postflight_vectors={"know": 0.8, "uncertainty": 0.2, "do": 0.85},
        db=db,
        phase_boundary=None,  # combined mode
        phase_tool_counts=None,
        work_type="remote-ops",
    )

    # Pipeline should return a dict with ungrounded holistic
    assert result is not None
    assert result.get("_internal_calibration_score") is None, "remote-ops should produce no holistic calibration score"

    # The combined phase result should be ungrounded_remote_ops
    combined = result.get("combined")
    if combined is not None:
        assert combined.get("calibration_status") == "ungrounded_remote_ops"
        assert combined.get("_internal_gaps", {}) == {}


def test_run_grounded_verification_remote_ops_phase_aware(tmp_path):
    """End-to-end: run_grounded_verification with work_type=remote-ops
    in phase-aware mode (with phase_boundary) produces ungrounded responses
    for both noetic and praxic phases."""
    from empirica.core.post_test.grounded_calibration import (
        run_grounded_verification,
    )

    db = _make_test_db(tmp_path)

    phase_boundary = {
        "has_check": True,
        "proceed_check_timestamp": 1000000.0,
        "preflight_timestamp": 999000.0,
        "noetic_only": False,
        "preflight_vectors": {"know": 0.6, "uncertainty": 0.3},
        "proceed_check_vectors": {"know": 0.75, "uncertainty": 0.22},
    }

    result = run_grounded_verification(
        session_id="rops-e2e-phased",
        postflight_vectors={"know": 0.85, "uncertainty": 0.18, "do": 0.9},
        db=db,
        phase_boundary=phase_boundary,
        phase_tool_counts={"noetic_tool_calls": 5, "praxic_tool_calls": 8},
        work_type="remote-ops",
    )

    assert result is not None
    # With phase_boundary, both phases run — both should return
    # ungrounded_remote_ops.
    assert result.get("_internal_calibration_score") is None
    assert result.get("_internal_holistic_gaps") == {}

    # Phase-specific results live under result["_internal_phases"]
    phases = result.get("_internal_phases", {})
    assert phases, "Expected phase results under result['_internal_phases']"

    for phase_name, phase_result in phases.items():
        assert phase_result.get("calibration_status") == "ungrounded_remote_ops", (
            f"Phase {phase_name} should be ungrounded_remote_ops, got {phase_result.get('calibration_status')}"
        )
        assert phase_result.get("_internal_gaps", {}) == {}
