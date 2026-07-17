"""Persistence test for goals.engagement_id — the goal↔engagement glue.

migration_051 added the nullable `engagement_id` column to goals; the
`goals-create --engagement-id <id>` flag stamps it. This exercises the
create/persist path end-to-end: a Goal carrying an engagement_id round-trips
into the `goals.engagement_id` column, and a goal created without one leaves
the column NULL.
"""

from __future__ import annotations

import uuid

from empirica.core.goals.repository import GoalRepository
from empirica.core.goals.types import Goal, ScopeVector, SuccessCriterion


def _repo(tmp_path) -> GoalRepository:
    return GoalRepository(db_path=str(tmp_path / "goals.db"))


def _make_goal(engagement_id: str | None) -> Goal:
    criterion = SuccessCriterion(
        id=str(uuid.uuid4()),
        description="Goal completion achieved",
        validation_method="completion",
    )
    return Goal.create(
        objective="scope me to an engagement",
        success_criteria=[criterion],
        scope=ScopeVector(breadth=0.3, duration=0.2, coordination=0.1),
        engagement_id=engagement_id,
    )


def test_engagement_id_persisted_when_provided(tmp_path):
    repo = _repo(tmp_path)
    eid = f"e-{uuid.uuid4().hex[:8]}"
    goal = _make_goal(eid)

    assert repo.save_goal(goal, session_id=str(uuid.uuid4())) is True

    row = repo.db.conn.execute("SELECT engagement_id FROM goals WHERE id = ?", (goal.id,)).fetchone()
    assert row[0] == eid
    # and it round-trips through the goal_data JSON too
    assert repo.get_goal(goal.id).engagement_id == eid
    repo.close()


def test_engagement_id_null_when_omitted(tmp_path):
    repo = _repo(tmp_path)
    goal = _make_goal(None)

    assert repo.save_goal(goal, session_id=str(uuid.uuid4())) is True

    row = repo.db.conn.execute("SELECT engagement_id FROM goals WHERE id = ?", (goal.id,)).fetchone()
    assert row[0] is None
    assert repo.get_goal(goal.id).engagement_id is None
    repo.close()
