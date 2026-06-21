"""
Tests for work_type-aware evidence exclusion in the grounded calibration mapper.

Verifies the fix for the metric-sycophancy / instrumentation-blindness bug
discovered 2026-04-07: prior versions of WORK_TYPE_RELEVANCE used source-id
keys (git_metrics, test_results, goal_completion, artifact_counts) that did
NOT match the actual source IDs emitted by collector.py (git, pytest, goals,
artifacts) — making the entire work_type scaling a silent no-op.

This test suite locks in the new semantics:
  - Source IDs in WORK_TYPE_RELEVANCE must match actual collector source IDs
  - Multiplier 0.0 means EXCLUDED (instrument-blind for this work_type)
  - Vectors with all-excluded evidence are reported as insufficient_evidence
  - Insufficient vectors are NOT in grounded, NOT in calibration_gaps
  - The trajectory tracker skips insufficient vectors entirely
"""

from __future__ import annotations

from empirica.core.post_test.collector import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
)
from empirica.core.post_test.mapper import (
    WORK_TYPE_RELEVANCE,
    EvidenceMapper,
    GroundedAssessment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_item(value: float, vectors: list[str]) -> EvidenceItem:
    return EvidenceItem(
        source="git",
        metric_name="lines_changed",
        value=value,
        raw_value=value,
        quality=EvidenceQuality.OBJECTIVE,
        supports_vectors=vectors,
    )


def _goals_item(value: float, vectors: list[str]) -> EvidenceItem:
    return EvidenceItem(
        source="goals",
        metric_name="completion_ratio",
        value=value,
        raw_value=value,
        quality=EvidenceQuality.OBJECTIVE,
        supports_vectors=vectors,
    )


def _artifacts_item(value: float, vectors: list[str]) -> EvidenceItem:
    return EvidenceItem(
        source="artifacts",
        metric_name="finding_count",
        value=value,
        raw_value=value,
        quality=EvidenceQuality.SEMI_OBJECTIVE,
        supports_vectors=vectors,
    )


def _bundle(items: list[EvidenceItem]) -> EvidenceBundle:
    return EvidenceBundle(
        session_id="test-session",
        items=items,
        sources_available=sorted({i.source for i in items}),
        coverage=0.5,
    )


# ---------------------------------------------------------------------------
# WORK_TYPE_RELEVANCE source ID alignment
# ---------------------------------------------------------------------------

ACTUAL_SOURCE_IDS = {
    # Universal collectors (collector.py)
    "noetic",
    "goals",
    "artifacts",
    "issues",
    "triage",
    "codebase_model",
    "non_git_files",
    "sentinel",
    "pytest",
    "git",
    "code_quality",
    # Prose profile collectors (prose_collector.py)
    "prose_quality",
    "document_metrics",
    "source_quality",
    "action_verification",
    # Web profile collector (web_collector.py)
    "web",
}


class TestWorkTypeRelevanceKeys:
    """The keys in WORK_TYPE_RELEVANCE must match actual source IDs from
    collector.py. Prior versions used names like 'git_metrics', 'pytest_results',
    'goal_completion', 'artifact_counts' that NEVER matched any source emitted
    by the collector — making the relevance scaling a silent no-op.
    """

    def test_all_keys_are_real_source_ids(self):
        """Every key in every work_type's relevance dict must be a real source ID."""
        for work_type, relevance in WORK_TYPE_RELEVANCE.items():
            for source_key in relevance:
                assert source_key in ACTUAL_SOURCE_IDS, (
                    f"WORK_TYPE_RELEVANCE['{work_type}'] uses unknown source "
                    f"'{source_key}'. Valid source IDs: {sorted(ACTUAL_SOURCE_IDS)}"
                )

    def test_release_excludes_git(self):
        """Release work is invisible to git_metrics — version sweep is mechanical."""
        assert WORK_TYPE_RELEVANCE["release"]["git"] == 0.0

    def test_release_excludes_code_quality(self):
        """Release doesn't change code, so code_quality is identical pre/post."""
        assert WORK_TYPE_RELEVANCE["release"]["code_quality"] == 0.0

    def test_audit_excludes_git(self):
        """Audits are read-only and don't commit."""
        assert WORK_TYPE_RELEVANCE["audit"]["git"] == 0.0

    def test_research_excludes_code_quality(self):
        """Research is exploratory, not code-quality-driven."""
        assert WORK_TYPE_RELEVANCE["research"]["code_quality"] == 0.0

    def test_comms_excludes_git(self):
        """Comms = sending messages, not committing code."""
        assert WORK_TYPE_RELEVANCE["comms"]["git"] == 0.0

    def test_code_baseline_has_no_overrides(self):
        """The code work_type is the baseline — no relevance adjustments."""
        assert WORK_TYPE_RELEVANCE["code"] == {}


# ---------------------------------------------------------------------------
# Exclusion semantics
# ---------------------------------------------------------------------------


class TestExclusionSemantic:
    """A relevance value of 0.0 means the source is filtered from the
    evidence pool entirely, not just down-weighted to near-zero."""

    def test_excluded_source_does_not_contribute_to_grounded(self):
        """For work_type=release, a git evidence item should be excluded entirely."""
        bundle = _bundle(
            [
                _git_item(0.05, ["do", "completion"]),  # excluded for release
                _goals_item(0.95, ["do", "completion"]),  # included
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"do": 1.0, "completion": 1.0},
            work_type="release",
        )

        # do/completion should reflect the goals item ONLY (~0.95),
        # NOT a weighted average of git=0.05 and goals=0.95.
        do_grounded = assessment.grounded["do"].estimated_value
        assert do_grounded > 0.9, (
            f"Expected do grounded ~0.95 from goals only, got {do_grounded} "
            f"— git source should have been excluded for release work"
        )

    def test_release_excluded_under_old_broken_keys_would_average(self):
        """Regression check: under the OLD broken behavior, git would have
        contributed at default 1.0 weight (because the dict key 'git_metrics'
        never matched the actual 'git' source). The result would have been
        an average ~0.5 — which is exactly the bias we observed. This test
        ensures that bug doesn't come back.
        """
        bundle = _bundle(
            [
                _git_item(0.0, ["do"]),  # would have dragged grounded down
                _goals_item(1.0, ["do"]),
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"do": 1.0},
            work_type="release",
        )
        # Under old bug: average ~0.5 (and gap ~0.5)
        # Under fix: ~1.0 (and gap ~0.0)
        assert assessment.grounded["do"].estimated_value > 0.9
        assert abs(assessment.calibration_gaps["do"]) < 0.1


# ---------------------------------------------------------------------------
# Insufficient evidence reporting
# ---------------------------------------------------------------------------


class TestInsufficientEvidence:
    """Vectors that had ALL their evidence excluded should be reported as
    insufficient_evidence and should NOT appear in grounded or gaps.
    """

    def test_vector_with_only_excluded_sources_is_insufficient(self):
        """If 'change' is only supported by git, and git is excluded, change
        should be insufficient — not silently zero."""
        bundle = _bundle(
            [
                _git_item(0.0, ["change"]),  # only source for change, excluded
                _goals_item(1.0, ["completion"]),  # different vector, included
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"change": 0.8, "completion": 1.0},
            work_type="release",
        )
        assert "change" in assessment.insufficient_evidence_vectors
        assert "change" not in assessment.grounded
        assert "change" not in assessment.calibration_gaps

    def test_insufficient_does_not_pollute_score(self):
        """A vector marked insufficient should not contribute to the
        overall calibration_score."""
        bundle = _bundle(
            [
                _git_item(0.0, ["change"]),
                _goals_item(1.0, ["completion"]),
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"change": 0.8, "completion": 1.0},
            work_type="release",
        )
        # completion gap is 0.0 (1.0 self vs 1.0 grounded). change is excluded.
        # Score should reflect only completion's perfect calibration.
        assert assessment.overall_calibration_score < 0.1

    def test_multi_source_vector_with_one_included_is_not_insufficient(self):
        """If a vector has evidence from BOTH excluded and included sources,
        it should still be grounded (using only the included source)."""
        bundle = _bundle(
            [
                _git_item(0.0, ["do"]),  # excluded
                _goals_item(1.0, ["do"]),  # included
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"do": 1.0},
            work_type="release",
        )
        assert "do" not in assessment.insufficient_evidence_vectors
        assert "do" in assessment.grounded
        assert assessment.grounded["do"].estimated_value > 0.9

    def test_default_work_type_does_not_exclude(self):
        """For work_type=code (or None), nothing is excluded — default
        behavior preserved."""
        bundle = _bundle(
            [
                _git_item(0.5, ["do"]),
                _goals_item(0.8, ["do"]),
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"do": 0.7},
            work_type="code",
        )
        assert assessment.insufficient_evidence_vectors == []
        assert "do" in assessment.grounded
        # Both sources contributed; grounded should be a weighted average.
        do_grounded = assessment.grounded["do"].estimated_value
        assert 0.5 <= do_grounded <= 0.8

    def test_no_work_type_does_not_exclude(self):
        """work_type=None preserves default (all sources at 1.0)."""
        bundle = _bundle(
            [
                _git_item(0.3, ["do"]),
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"do": 0.5},
            work_type=None,
        )
        assert assessment.insufficient_evidence_vectors == []
        assert "do" in assessment.grounded


# ---------------------------------------------------------------------------
# Trajectory tracker integration
# ---------------------------------------------------------------------------


class TestTrajectoryTrackerSkipsInsufficient:
    """The trajectory tracker should skip insufficient_evidence_vectors
    entirely — not even write NULL rows. This prevents historical drift
    accumulation for instrument-blind work_types.
    """

    def test_insufficient_vectors_not_written_to_trajectory(self):
        """Verify that record_trajectory_point() does not write rows for
        vectors in insufficient_evidence_vectors."""
        from empirica.core.post_test.mapper import GroundedVectorEstimate
        from empirica.core.post_test.trajectory_tracker import TrajectoryTracker

        # Mock db: cursor that captures executed SQL
        class FakeCursor:
            def __init__(self):
                self.executed = []
                self._next_fetch = ("test-ai",)

            def execute(self, sql, params=()):
                self.executed.append((sql.strip().split()[0], params))
                return self

            def fetchone(self):
                return self._next_fetch

        class FakeConn:
            def __init__(self):
                self.cur = FakeCursor()

            def cursor(self):
                return self.cur

            def commit(self):
                pass

        class FakeDB:
            def __init__(self):
                self.conn = FakeConn()

        # Build an assessment where 'change' is insufficient and 'do' is grounded
        assessment = GroundedAssessment(
            session_id="test-session",
            self_assessed={"do": 1.0, "change": 0.8},
            grounded={
                "do": GroundedVectorEstimate(
                    vector_name="do",
                    estimated_value=0.95,
                    confidence=0.9,
                    evidence_count=1,
                    primary_source="goals",
                ),
            },
            calibration_gaps={"do": 0.05},
            grounded_coverage=0.5,
            overall_calibration_score=0.05,
            phase="praxic",
            insufficient_evidence_vectors=["change"],
        )

        db = FakeDB()
        tracker = TrajectoryTracker(db)
        recorded = tracker.record_trajectory_point("test-session", assessment, phase="praxic")

        # Only 'do' should be recorded (1 row), not 'change'
        assert recorded == 1
        # Inspect the INSERT params — only 'do' should appear as vector_name
        inserts = [p for cmd, p in db.conn.cur.executed if cmd == "INSERT"]
        assert len(inserts) == 1
        # vector_name is the 4th positional param (index 3)
        assert inserts[0][3] == "do"


# ---------------------------------------------------------------------------
# Regression: drift pattern from this session
# ---------------------------------------------------------------------------


class TestSessionDriftRegression:
    """Reproduces the specific bias pattern observed in session 659f0619 on
    2026-04-07. With the fix, release-typed transactions should NOT show
    the +0.55 completion gap and +0.35 do gap that were polluting calibration
    history under the broken work_type scaling.
    """

    def test_release_completion_no_longer_shows_systematic_bias(self):
        """The 14 release-related POSTFLIGHTs in session 659f0619 showed:
            mean self_assessed completion = 1.0
            mean grounded completion = 0.45 (gap +0.55)
        The fix should make this gap go to ~0 by excluding git/code_quality
        which were dragging the grounded value down.
        """
        # Simulate the conditions: high self-report, git sees no changes
        # (mechanical version sweep), goals show completion.
        bundle = _bundle(
            [
                _git_item(0.0, ["completion", "do", "change"]),  # version sweep
                _goals_item(1.0, ["completion", "do"]),  # release goal complete
                _artifacts_item(0.7, ["completion"]),  # findings logged
            ]
        )
        mapper = EvidenceMapper()
        assessment = mapper.map_evidence(
            bundle,
            self_assessed_vectors={"completion": 1.0, "do": 1.0, "change": 0.5},
            work_type="release",
        )

        # completion: git excluded, goals (1.0) and artifacts (0.7) contribute.
        # Grounded should be weighted toward goals (higher weight via 1.5 multiplier).
        completion_gap = abs(assessment.calibration_gaps.get("completion", 1.0))
        assert completion_gap < 0.3, (
            f"Expected completion gap < 0.3 after fix, got {completion_gap}. "
            f"This means git is still pulling completion down."
        )

        # change: git was the ONLY source. Under fix, change should be insufficient.
        assert "change" in assessment.insufficient_evidence_vectors, (
            "change should be insufficient (only sourced from excluded git)"
        )


# ---------------------------------------------------------------------------
# 2026-04-08 — calibration_status field on GroundedAssessment
# (Sentinel measurer remote-ops design, Task 2)
# ---------------------------------------------------------------------------


def test_grounded_assessment_has_calibration_status_field():
    """GroundedAssessment dataclass should expose calibration_status with
    a default of 'grounded' for the normal calibration path."""
    assessment = GroundedAssessment(
        session_id="test-session",
        self_assessed={"know": 0.7},
        grounded={},
        calibration_gaps={},
        grounded_coverage=0.0,
        overall_calibration_score=0.0,
    )
    assert hasattr(assessment, "calibration_status")
    assert assessment.calibration_status == "grounded"


def test_grounded_assessment_calibration_status_explicit():
    """calibration_status can be set explicitly to non-grounded values."""
    for status in ("grounded", "insufficient_evidence", "ungrounded_remote_ops"):
        assessment = GroundedAssessment(
            session_id="test-session",
            self_assessed={"know": 0.7},
            grounded={},
            calibration_gaps={},
            grounded_coverage=0.0,
            overall_calibration_score=0.0,
            calibration_status=status,
        )
        assert assessment.calibration_status == status


# ---------------------------------------------------------------------------
# 2026-04-08 — remote-ops work_type relevance entry (Task 4)
# ---------------------------------------------------------------------------


def test_remote_ops_relevance_entry_exists():
    """remote-ops should be present in WORK_TYPE_RELEVANCE."""
    assert "remote-ops" in WORK_TYPE_RELEVANCE


def test_remote_ops_relevance_zeros_all_known_sources():
    """Every relevance value for remote-ops must be 0.0 — it is the
    'on/off switch' for ungroundable work."""
    weights = WORK_TYPE_RELEVANCE["remote-ops"]
    for source, weight in weights.items():
        assert weight == 0.0, (
            f"remote-ops {source} should be 0.0, got {weight} — "
            f"remote-ops is the on/off switch and must zero every source"
        )


def test_remote_ops_covers_all_known_collector_sources():
    """The remote-ops entry should cover every source that any collector
    might emit, so nothing slips through with the default 1.0 multiplier."""
    weights = WORK_TYPE_RELEVANCE["remote-ops"]
    expected_sources = {
        "artifacts",
        "noetic",
        "sentinel",
        "goals",
        "issues",
        "triage",
        "codebase_model",
        "non_git_files",
        "git",
        "code_quality",
        "pytest",
        "source_quality",
        "prose_quality",
        "document_metrics",
        "action_verification",
        "web",
    }
    missing = expected_sources - set(weights.keys())
    assert not missing, (
        f"remote-ops is missing relevance entries for: {missing}. "
        f"Without them, those sources would default to 1.0 multiplier and "
        f"contribute evidence — defeating the on/off switch."
    )
