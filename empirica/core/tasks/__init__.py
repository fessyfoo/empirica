#!/usr/bin/env python3
"""
Task Decomposition Module

Provides task breakdown and management for goal achievement.
MVP implementation focuses on explicit task creation (AI creates tasks via MCP).
"""

from .repository import TaskRepository
from .types import EpistemicImportance, SubTask, TaskDecomposition, TaskStatus

__all__ = ["EpistemicImportance", "SubTask", "TaskDecomposition", "TaskRepository", "TaskStatus"]
