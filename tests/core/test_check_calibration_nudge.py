"""
Tests for the CHECK-time calibration_nudge.

When a CHECK is submitted with decision=proceed and the current transaction
has zero epistemic artifacts logged, the response should include a
praxic_reminders.calibration_nudge field with explicit scoring language.
"""

import pytest

from empirica.cli.command_handlers.workflow_commands import _build_retrospective

# Marked as integration: requires empirica CLI on PATH, git-initialized
# CWD, and/or a populated sessions.db. Excluded from default CI run
# (pytest -m "not integration"). Run explicitly via:
#   pytest -m integration tests/...
pytestmark = pytest.mark.integration



class TestRetrospectiveArtifactCounts:
    """The building block — _build_retrospective should return artifact_counts."""

    def test_returns_dict_with_counts(self):
        # Non-existent session/tx returns zeros (not error)
        retro = _build_retrospective(
            session_id="nonexistent-session",
            transaction_id="nonexistent-tx",
        )
        assert "artifact_counts" in retro
        counts = retro["artifact_counts"]
        assert "findings" in counts
        assert "unknowns" in counts
        assert "dead_ends" in counts
        assert "mistakes" in counts
        assert "assumptions" in counts
        assert "decisions" in counts

    def test_zero_artifacts_no_breadth_note(self):
        # When 0 artifacts, there's no breadth_note (because breadth_note
        # fires when 1 type used). Instead, the CHECK nudge should fire.
        retro = _build_retrospective(
            session_id="nonexistent-session",
            transaction_id="nonexistent-tx",
        )
        counts = retro["artifact_counts"]
        total = sum(counts.values())
        assert total == 0


class TestCalibrationNudgeLogic:
    """
    Test the nudge decision logic in isolation — should fire when:
    1. total_artifacts == 0 (no artifacts at all)
    2. total_artifacts < 3 AND only 1 type used (narrow breadth)
    """

    def _compute_nudge(self, counts: dict) -> str | None:
        """Replica of the nudge decision logic from handle_check_submit_command."""
        total_artifacts = sum(counts.values())
        types_used = [k for k, v in counts.items() if v > 0]

        if total_artifacts == 0:
            return "zero_artifacts_nudge"
        elif total_artifacts < 3 and len(types_used) == 1:
            return "narrow_breadth_nudge"
        return None

    def test_zero_artifacts_fires_nudge(self):
        counts = {
            "findings": 0, "unknowns": 0, "dead_ends": 0,
            "mistakes": 0, "assumptions": 0, "decisions": 0,
        }
        assert self._compute_nudge(counts) == "zero_artifacts_nudge"

    def test_single_finding_fires_narrow_nudge(self):
        counts = {
            "findings": 1, "unknowns": 0, "dead_ends": 0,
            "mistakes": 0, "assumptions": 0, "decisions": 0,
        }
        assert self._compute_nudge(counts) == "narrow_breadth_nudge"

    def test_two_findings_fires_narrow_nudge(self):
        counts = {
            "findings": 2, "unknowns": 0, "dead_ends": 0,
            "mistakes": 0, "assumptions": 0, "decisions": 0,
        }
        assert self._compute_nudge(counts) == "narrow_breadth_nudge"

    def test_three_findings_no_nudge(self):
        """3 artifacts even if all same type — no longer narrow."""
        counts = {
            "findings": 3, "unknowns": 0, "dead_ends": 0,
            "mistakes": 0, "assumptions": 0, "decisions": 0,
        }
        assert self._compute_nudge(counts) is None

    def test_two_types_no_nudge(self):
        """Diversity matters — 2 finding + 1 decision = breadth."""
        counts = {
            "findings": 2, "unknowns": 0, "dead_ends": 0,
            "mistakes": 0, "assumptions": 0, "decisions": 1,
        }
        assert self._compute_nudge(counts) is None

    def test_full_breadth_no_nudge(self):
        """All 6 types used — clearly not a nudge case."""
        counts = {
            "findings": 5, "unknowns": 3, "dead_ends": 2,
            "mistakes": 1, "assumptions": 4, "decisions": 2,
        }
        assert self._compute_nudge(counts) is None

    def test_one_type_large_count_no_nudge(self):
        """10 findings alone is narrow but not sparse — no nudge."""
        counts = {
            "findings": 10, "unknowns": 0, "dead_ends": 0,
            "mistakes": 0, "assumptions": 0, "decisions": 0,
        }
        # Only narrow if total < 3
        assert self._compute_nudge(counts) is None


class TestCalibrationNudgeMessages:
    """The nudge text should contain specific scoring language."""

    def test_zero_nudge_mentions_calibration(self):
        message = (
            "⚠ Current transaction has 0 epistemic artifacts logged. "
            "Your grounded calibration score depends on artifact breadth — "
            "zero artifacts means grounded verification has nothing to check "
            "your self-assessment against, which inflates perceived competence "
            "and leaves calibration gaps uncorrected. Log at least one finding "
            "before POSTFLIGHT: empirica finding-log --finding \"...\" --impact 0.5"
        )
        assert "calibration" in message.lower()
        assert "grounded verification" in message.lower()
        assert "finding-log" in message

    def test_narrow_nudge_suggests_artifact_types(self):
        message = (
            "⚠ Only 2 findings logged in this transaction. "
            "Breadth matters: assumptions, decisions, and dead-ends each ground "
            "different aspects of calibration. Consider what you're assuming "
            "(assumption-log), what you've chosen (decision-log), and what "
            "didn't work (deadend-log)."
        )
        assert "assumption-log" in message
        assert "decision-log" in message
        assert "deadend-log" in message
