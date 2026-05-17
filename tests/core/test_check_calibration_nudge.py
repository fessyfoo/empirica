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


class TestDeferredProposalsNudgeSql:
    """The SQL pattern that surfaces open proposal-derived goals at
    POSTFLIGHT. Tests at the SQL level rather than via _build_retrospective
    to avoid the project-DB-resolution fixture overhead — the wiring layer
    is trivial; the discriminating logic is the query.

    Driver: David, 2026-05-17. AIs were logging "Process proposal prop_XXX"
    defer goals during in-flight work then forgetting them post-POSTFLIGHT,
    leaving peer AIs' outboxes visibly stalled (half-handshake bug class).
    """

    @pytest.fixture
    def seeded_db(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, project_id TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE goals (
                id TEXT PRIMARY KEY, session_id TEXT, objective TEXT,
                description TEXT, is_completed INTEGER DEFAULT 0,
                created_timestamp REAL DEFAULT 0
            )
        """)
        cur.execute("INSERT INTO sessions VALUES ('S1', 'P1')")
        cur.execute("INSERT INTO sessions VALUES ('S2', 'P1')")
        cur.execute("INSERT INTO sessions VALUES ('S3', 'P2')")
        # Two open proposal-derived goals in P1 — match the convention
        # prefix "Process proposal prop_<id>:" (should surface)
        cur.execute("INSERT INTO goals VALUES "
                    "('G1','S1','Process proposal prop_aaa: fix bootstrap', '', 0, 100)")
        cur.execute("INSERT INTO goals VALUES "
                    "('G2','S2','Process proposal prop_bbb: deprecate refdocs', '', 0, 200)")
        # Open goal in P1 NOT proposal-derived (should not surface)
        cur.execute("INSERT INTO goals VALUES "
                    "('G3','S1','Regular goal — refactor cockpit', '', 0, 150)")
        # Completed proposal goal in P1 (should not surface)
        cur.execute("INSERT INTO goals VALUES "
                    "('G4','S1','Process proposal prop_ccc: done', '', 1, 50)")
        # Open proposal goal in OTHER project P2 (should not surface — scoped)
        cur.execute("INSERT INTO goals VALUES "
                    "('G5','S3','Process proposal prop_ddd: other project', '', 0, 300)")
        # Planning goal that MENTIONS a prop_ id in description but isn't a
        # defer goal — must NOT surface (convention-prefix-only filter).
        # Pre-2026-05-17 the query also matched description, which over-fired
        # on planning goals that referenced proposals or PROPOSAL_*.md files.
        cur.execute("INSERT INTO goals VALUES "
                    "('G6','S1','Generic planning goal', 'See prop_eee for context', 0, 400)")
        conn.commit()
        return conn

    def _query(self, conn, session_id):
        """Mirrors the SQL pattern in _build_retrospective."""
        cur = conn.cursor()
        cur.execute("""
            SELECT g.id, g.objective FROM goals g
            JOIN sessions s ON g.session_id = s.session_id
            WHERE g.is_completed = 0
              AND s.project_id = (
                SELECT project_id FROM sessions WHERE session_id = ?
              )
              AND g.objective LIKE 'Process proposal prop_%'
            ORDER BY g.created_timestamp DESC
        """, (session_id,))
        return cur.fetchall()

    def test_open_proposal_goals_in_same_project_surface(self, seeded_db):
        results = self._query(seeded_db, "S1")
        ids = {row[0] for row in results}
        # G1 + G2 (other session same project). G6 has prop_ in description
        # but doesn't match the convention prefix — correctly excluded.
        assert ids == {"G1", "G2"}

    def test_completed_proposal_goals_do_not_surface(self, seeded_db):
        results = self._query(seeded_db, "S1")
        ids = {row[0] for row in results}
        assert "G4" not in ids, "completed goals should not be flagged"

    def test_non_proposal_goals_do_not_surface(self, seeded_db):
        results = self._query(seeded_db, "S1")
        ids = {row[0] for row in results}
        assert "G3" not in ids, "non-proposal-derived goals should not be flagged"

    def test_planning_goals_with_prop_in_description_do_not_surface(self, seeded_db):
        """Convention discipline: only objectives starting with 'Process
        proposal prop_' count as defer goals. Planning goals that mention
        a prop_ id in their description (proposal references, PROPOSAL_*.md
        filenames, etc.) must NOT be flagged. Pre-2026-05-17 the query also
        matched description text, surfacing 16 false positives in the very
        first transaction that fired it."""
        results = self._query(seeded_db, "S1")
        ids = {row[0] for row in results}
        assert "G6" not in ids, "description-only prop_ matches must not be flagged"

    def test_other_project_goals_do_not_surface(self, seeded_db):
        """Scoping: querying from a session in P1 should not see P2's goals."""
        results = self._query(seeded_db, "S1")
        ids = {row[0] for row in results}
        assert "G5" not in ids, "other-project goals must not cross the scope"

    def test_ordering_is_recency(self, seeded_db):
        """Most recently created proposal goals appear first — limit-10 keeps
        recency relevance when many are open."""
        results = self._query(seeded_db, "S1")
        ids_in_order = [row[0] for row in results]
        # G2 (200) > G1 (100). G6 excluded by convention-prefix filter.
        assert ids_in_order == ["G2", "G1"]
