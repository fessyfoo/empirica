#!/usr/bin/env python3
"""
Completion Tracking Module

Provides progress tracking and completion verification for goals and tasks.
Phase 2: Git notes integration for team coordination and lead AI queries.
"""

from .git_query import GitProgressQuery
from .tracker import CompletionTracker
from .types import CompletionMetrics, CompletionRecord

__all__ = ["CompletionMetrics", "CompletionRecord", "CompletionTracker", "GitProgressQuery"]
