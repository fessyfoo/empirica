"""Tests for the --success-criteria entry parser (G4) + add_success_criterion SDK.

Covers:
  - Bare-string entries default to validation_method=completion (back-compat)
  - "method:metric@op:threshold" parses cleanly
  - Whitespace is tolerated
  - Negative thresholds are accepted
  - Malformed expressions fall back to bare-string completion (don't crash)
  - dict entries are passed through
  - GoalRepository.add_success_criterion writes to both normalized table + JSON
"""

from __future__ import annotations

import uuid

import pytest

from empirica.cli.command_handlers.goal_commands import _parse_criterion_entry
from empirica.core.goals.repository import GoalRepository
from empirica.core.goals.types import Goal, ScopeVector, SuccessCriterion

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_bare_string_defaults_to_completion():
    sc = _parse_criterion_entry("All subtasks complete")
    assert sc.validation_method == "completion"
    assert sc.description == "All subtasks complete"
    assert sc.threshold is None
    assert sc.is_required is True


def test_typed_quality_gate_with_lower_op():
    sc = _parse_criterion_entry("quality_gate:prose_stylometry_adherence@<=0.25")
    assert sc.validation_method == "quality_gate"
    assert sc.description == "prose_stylometry_adherence"
    assert sc.threshold == 0.25


def test_typed_metric_threshold_with_higher_op():
    sc = _parse_criterion_entry("metric_threshold:coherence@>=0.7")
    assert sc.validation_method == "metric_threshold"
    assert sc.description == "coherence"
    assert sc.threshold == 0.7


def test_typed_completion_with_threshold():
    sc = _parse_criterion_entry("completion:subtask_ratio@>=0.9")
    assert sc.validation_method == "completion"
    assert sc.description == "subtask_ratio"
    assert sc.threshold == 0.9


def test_whitespace_tolerated():
    sc = _parse_criterion_entry("  quality_gate : my_metric @ <= 0.5  ")
    assert sc.validation_method == "quality_gate"
    assert sc.description == "my_metric"
    assert sc.threshold == 0.5


def test_negative_threshold_accepted():
    """Some metrics could meaningfully be negative (e.g. delta from baseline)."""
    sc = _parse_criterion_entry("metric_threshold:delta@>=-0.1")
    assert sc.threshold == -0.1


def test_metric_with_dots_and_dashes():
    """Metric names like 'ruff.density' or 'test-coverage' should parse."""
    sc = _parse_criterion_entry("quality_gate:ruff.density@<=0.05")
    assert sc.description == "ruff.density"
    assert sc.threshold == 0.05


def test_malformed_falls_back_to_completion():
    """Anything that doesn't match the regex is treated as a bare description."""
    sc = _parse_criterion_entry("not a valid expression")
    assert sc.validation_method == "completion"
    assert sc.description == "not a valid expression"
    assert sc.threshold is None


def test_partial_expression_falls_back():
    """'method:metric' without @op:threshold falls back to bare string."""
    sc = _parse_criterion_entry("quality_gate:my_metric")
    assert sc.validation_method == "completion"
    assert sc.description == "quality_gate:my_metric"


def test_invalid_op_falls_back():
    """Op other than <= / >= falls back to bare string."""
    sc = _parse_criterion_entry("quality_gate:metric@==0.5")
    assert sc.validation_method == "completion"


def test_dict_entry_passed_through():
    sc = _parse_criterion_entry({
        "description": "custom",
        "validation_method": "quality_gate",
        "threshold": 0.42,
        "is_required": False,
    })
    assert sc.validation_method == "quality_gate"
    assert sc.description == "custom"
    assert sc.threshold == 0.42
    assert sc.is_required is False


def test_dict_entry_defaults_when_partial():
    sc = _parse_criterion_entry({"description": "minimal"})
    assert sc.validation_method == "completion"
    assert sc.description == "minimal"
    assert sc.is_required is True
    assert sc.is_met is False


def test_each_parsed_criterion_has_unique_id():
    a = _parse_criterion_entry("foo")
    b = _parse_criterion_entry("foo")
    assert a.id != b.id


# ---------------------------------------------------------------------------
# add_success_criterion SDK helper
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_session(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    repo = GoalRepository(db_path=db_path)
    session_id = "test-session-x"
    repo.db.conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT,
            ai_id TEXT
        )
    """)
    repo.db.conn.execute(
        "INSERT OR IGNORE INTO sessions (session_id, project_id, ai_id) VALUES (?, ?, ?)",
        (session_id, None, "claude-code"),
    )
    repo.db.conn.commit()
    return repo, session_id


def _make_goal_with_one_criterion() -> Goal:
    return Goal(
        id=str(uuid.uuid4()),
        objective="parser test goal",
        success_criteria=[
            SuccessCriterion(
                id=str(uuid.uuid4()),
                description="initial",
                validation_method="completion",
                is_required=True,
                is_met=False,
            )
        ],
        scope=ScopeVector(0.3, 0.2, 0.1),
    )


def test_add_success_criterion_persists_to_normalized_table(repo_with_session):
    repo, session_id = repo_with_session
    goal = _make_goal_with_one_criterion()
    repo.save_goal(goal, session_id=session_id)

    new_id = repo.add_success_criterion(
        goal.id,
        validation_method="quality_gate",
        description="my_metric",
        threshold=0.5,
        is_required=True,
    )
    assert new_id is not None

    cursor = repo.db.conn.execute(
        "SELECT description, validation_method, threshold FROM success_criteria WHERE id = ?",
        (new_id,),
    )
    row = cursor.fetchone()
    assert row[0] == "my_metric"
    assert row[1] == "quality_gate"
    assert row[2] == 0.5


def test_add_success_criterion_syncs_goal_data_json(repo_with_session):
    repo, session_id = repo_with_session
    goal = _make_goal_with_one_criterion()
    repo.save_goal(goal, session_id=session_id)

    new_id = repo.add_success_criterion(
        goal.id, "metric_threshold", "coherence", threshold=0.7,
    )

    reloaded = repo.get_goal(goal.id)
    assert reloaded is not None
    crit_ids = [c.id for c in reloaded.success_criteria]
    assert new_id in crit_ids
    new_crit = next(c for c in reloaded.success_criteria if c.id == new_id)
    assert new_crit.description == "coherence"
    assert new_crit.validation_method == "metric_threshold"
    assert new_crit.threshold == 0.7


def test_add_success_criterion_returns_none_for_missing_goal(repo_with_session):
    repo, _ = repo_with_session
    result = repo.add_success_criterion(
        "does-not-exist", "completion", "nope",
    )
    assert result is None


def test_add_success_criterion_threshold_optional(repo_with_session):
    """Completion criteria often have no threshold — None should be persisted."""
    repo, session_id = repo_with_session
    goal = _make_goal_with_one_criterion()
    repo.save_goal(goal, session_id=session_id)

    new_id = repo.add_success_criterion(
        goal.id, "completion", "auxiliary subtasks done",
    )
    assert new_id is not None

    cursor = repo.db.conn.execute(
        "SELECT threshold FROM success_criteria WHERE id = ?", (new_id,)
    )
    assert cursor.fetchone()[0] is None
