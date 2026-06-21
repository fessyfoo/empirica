#!/usr/bin/env python3
"""
Goal Management Module

Provides structured goal tracking with success criteria, dependencies, and constraints.
MVP implementation focuses on explicit goal creation (AI creates goals directly via MCP).
"""

from .repository import GoalRepository
from .types import Dependency, DependencyType, Goal, ScopeVector, SuccessCriterion

__all__ = ["Dependency", "DependencyType", "Goal", "GoalRepository", "ScopeVector", "SuccessCriterion"]
