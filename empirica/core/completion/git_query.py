#!/usr/bin/env python3
"""
Git Progress Query - Query git notes for team progress tracking

Enables lead AIs to query git log for task completion and progress tracking.
Combines task metadata from git notes with commit history.

Phase 2 Implementation: Unified audit trail via git notes
"""

import json
import logging
import subprocess
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class GitProgressQuery:
    """
    Query git notes for team progress tracking

    Lead AI can query git to see what agents accomplished, when, and why.
    Combines task metadata with commit history for unified timeline.
    """

    def __init__(self):
        """Initialize git query interface"""
        self.git_available = self._check_git_available()

    def _check_git_available(self) -> bool:
        """Check if git is available"""
        try:
            result = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, timeout=2, cwd=".")
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def get_goal_timeline(self, goal_id: str, max_commits: int = 100) -> dict[str, Any]:
        """
        Get commit timeline for goal with task metadata

        Args:
            goal_id: Goal UUID to query
            max_commits: Maximum commits to retrieve

        Returns:
            Timeline dict with commits and task metadata
        """
        if not self.git_available:
            return {"goal_id": goal_id, "error": "Git not available", "commits": []}

        try:
            # Get commits with notes from goal-specific ref
            note_ref = f"empirica/tasks/{goal_id}"

            result = subprocess.run(
                ["git", "log", f"--max-count={max_commits}", "--format=%H|%at|%s", f"--notes={note_ref}"],
                capture_output=True,
                timeout=10,
                cwd=".",
                text=True,
            )

            if result.returncode != 0:
                return {"goal_id": goal_id, "error": f"Git log failed: {result.stderr}", "commits": []}

            # Parse commits
            commits = []
            for line in result.stdout.strip().split("\n"):
                if not line or line.startswith("Notes"):
                    continue

                parts = line.split("|", 2)
                if len(parts) < 3:
                    continue

                commit_hash, timestamp, message = parts

                # Try to get task note for this commit
                task_note = self._get_task_note(commit_hash, note_ref)

                commit_data = {
                    "hash": commit_hash[:7],
                    "full_hash": commit_hash,
                    "timestamp": int(timestamp),
                    "datetime": datetime.fromtimestamp(int(timestamp)).isoformat(),
                    "message": message,
                    "task": task_note,
                }

                commits.append(commit_data)

            # Get completed subtask IDs
            completed_subtasks = [c["task"]["subtask_id"] for c in commits if c["task"] is not None]

            return {
                "goal_id": goal_id,
                "commits": commits,
                "total_commits": len(commits),
                "completed_subtasks": completed_subtasks,
                "completion_count": len(completed_subtasks),
            }

        except Exception as e:
            logger.error(f"Error querying goal timeline: {e}")
            return {"goal_id": goal_id, "error": str(e), "commits": []}

    def _get_task_note(self, commit_hash: str, note_ref: str) -> dict[str, Any] | None:
        """
        Get task note for a specific commit

        Args:
            commit_hash: Commit hash
            note_ref: Git notes reference

        Returns:
            Task metadata dict or None
        """
        try:
            result = subprocess.run(
                ["git", "notes", "--ref", note_ref, "show", commit_hash],
                capture_output=True,
                timeout=2,
                cwd=".",
                text=True,
            )

            if result.returncode != 0:
                return None

            # Parse JSON note
            note_data = json.loads(result.stdout)
            return note_data

        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None
        except Exception as e:
            logger.debug(f"Error getting task note: {e}")
            return None

    def get_team_progress(self, goal_ids: list[str]) -> dict[str, Any]:
        """
        Multi-goal progress for team coordination

        Lead AI can see progress across multiple agents/goals

        Args:
            goal_ids: List of goal UUIDs

        Returns:
            Team progress summary
        """
        if not self.git_available:
            return {"error": "Git not available", "goals": []}

        team_data: dict[str, Any] = {"goals": [], "total_completed_tasks": 0, "total_commits": 0}

        for goal_id in goal_ids:
            goal_timeline = self.get_goal_timeline(goal_id)

            team_data["goals"].append(
                {
                    "goal_id": goal_id,
                    "completed_tasks": goal_timeline.get("completion_count", 0),
                    "total_commits": goal_timeline.get("total_commits", 0),
                    "last_commit": goal_timeline["commits"][0] if goal_timeline.get("commits") else None,
                }
            )

            team_data["total_completed_tasks"] += goal_timeline.get("completion_count", 0)
            team_data["total_commits"] += goal_timeline.get("total_commits", 0)

        return team_data

    def get_unified_timeline(self, session_id: str, goal_id: str) -> dict[str, Any]:
        """
        Combine task metadata with epistemic state

        Shows complete agent journey: goals → actions → learning

        Args:
            session_id: Session UUID
            goal_id: Goal UUID

        Returns:
            Unified timeline with tasks + epistemic checkpoints
        """
        if not self.git_available:
            return {"error": "Git not available", "timeline": []}

        try:
            # Get task timeline
            task_timeline = self.get_goal_timeline(goal_id)

            # Get epistemic checkpoints from session notes
            epistemic_ref = f"empirica/session/{session_id}"

            subprocess.run(
                ["git", "log", "--max-count=100", "--format=%H|%at", f"--notes={epistemic_ref}"],
                capture_output=True,
                timeout=10,
                cwd=".",
                text=True,
            )

            # Build unified timeline
            timeline = []

            for commit_data in task_timeline.get("commits", []):
                entry = {
                    "type": "commit",
                    "timestamp": commit_data["timestamp"],
                    "datetime": commit_data["datetime"],
                    "commit_hash": commit_data["hash"],
                    "message": commit_data["message"],
                }

                # Add task metadata if present
                if commit_data.get("task"):
                    entry["task"] = {
                        "subtask_id": commit_data["task"]["subtask_id"],
                        "description": commit_data["task"]["description"],
                        "epistemic_importance": commit_data["task"]["epistemic_importance"],
                    }

                # Try to get epistemic checkpoint for this commit
                epistemic_note = self._get_task_note(commit_data["full_hash"], epistemic_ref)

                if epistemic_note:
                    entry["epistemic_state"] = {
                        "know": epistemic_note.get("vectors", {}).get("know"),
                        "uncertainty": epistemic_note.get("vectors", {}).get("uncertainty"),
                        "phase": epistemic_note.get("phase"),
                    }

                timeline.append(entry)

            # Sort by timestamp descending
            timeline.sort(key=lambda x: x["timestamp"], reverse=True)

            return {"session_id": session_id, "goal_id": goal_id, "timeline": timeline, "total_events": len(timeline)}

        except Exception as e:
            logger.error(f"Error building unified timeline: {e}")
            return {"error": str(e), "timeline": []}

    def get_recent_activity(self, hours: int = 24) -> dict[str, Any]:
        """
        Get recent activity across all goals

        Useful for lead AI to see what's happening now

        Args:
            hours: Hours to look back

        Returns:
            Recent activity summary
        """
        if not self.git_available:
            return {"error": "Git not available", "activity": []}

        try:
            # Get recent commits
            result = subprocess.run(
                ["git", "log", f"--since={hours} hours ago", "--format=%H|%at|%s"],
                capture_output=True,
                timeout=5,
                cwd=".",
                text=True,
            )

            if result.returncode != 0:
                return {"error": "Git log failed", "activity": []}

            activity = []

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("|", 2)
                if len(parts) < 3:
                    continue

                commit_hash, timestamp, message = parts

                # Check for task notes in any goal namespace
                # (We'd need to iterate through known goals, but for now just report commits)
                activity.append(
                    {
                        "commit_hash": commit_hash[:7],
                        "timestamp": int(timestamp),
                        "datetime": datetime.fromtimestamp(int(timestamp)).isoformat(),
                        "message": message,
                    }
                )

            return {"hours": hours, "activity": activity, "commit_count": len(activity)}

        except Exception as e:
            logger.error(f"Error getting recent activity: {e}")
            return {"error": str(e), "activity": []}
