#!/usr/bin/env python3
"""
Completion Type Definitions

Core dataclasses for tracking goal and task completion.
MVP design: Simple completion tracking with manual evidence recording.
"""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompletionRecord:
    """
    Completion status for a goal

    Tracks progress, completed/remaining tasks, and evidence.
    """

    goal_id: str
    completion_percentage: float  # 0.0 to 1.0
    completed_subtasks: list[str]  # SubTask IDs
    remaining_subtasks: list[str]  # SubTask IDs
    blocked_subtasks: list[str]  # SubTask IDs
    estimated_remaining_tokens: int
    actual_tokens_used: int
    completion_evidence: dict[str, str]  # subtask_id -> evidence (commit hash, file path, etc.)
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "goal_id": self.goal_id,
            "completion_percentage": self.completion_percentage,
            "completed_subtasks": self.completed_subtasks,
            "remaining_subtasks": self.remaining_subtasks,
            "blocked_subtasks": self.blocked_subtasks,
            "estimated_remaining_tokens": self.estimated_remaining_tokens,
            "actual_tokens_used": self.actual_tokens_used,
            "completion_evidence": self.completion_evidence,
            "last_updated": self.last_updated,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CompletionRecord":
        """Deserialize from dictionary"""
        return CompletionRecord(
            goal_id=data["goal_id"],
            completion_percentage=data["completion_percentage"],
            completed_subtasks=data["completed_subtasks"],
            remaining_subtasks=data["remaining_subtasks"],
            blocked_subtasks=data["blocked_subtasks"],
            estimated_remaining_tokens=data["estimated_remaining_tokens"],
            actual_tokens_used=data["actual_tokens_used"],
            completion_evidence=data["completion_evidence"],
            last_updated=data.get("last_updated", time.time()),
        )


@dataclass
class CompletionMetrics:
    """
    Aggregate completion metrics across goals

    Useful for session-level or project-level tracking.
    """

    goals_completed: int
    goals_in_progress: int
    goals_blocked: int
    total_tokens_used: int
    average_completion_rate: float
    efficiency_score: float  # actual vs estimated tokens (lower is better)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "goals_completed": self.goals_completed,
            "goals_in_progress": self.goals_in_progress,
            "goals_blocked": self.goals_blocked,
            "total_tokens_used": self.total_tokens_used,
            "average_completion_rate": self.average_completion_rate,
            "efficiency_score": self.efficiency_score,
        }
