"""
Empirica Lessons - Epistemic Procedural Knowledge System

This module provides infrastructure for storing, retrieving, and executing
procedural knowledge with epistemic metadata. Lessons capture not just HOW
to do something, but the epistemic state changes that result.

Key Concepts:
- Lesson: A procedural sequence with epistemic metadata
- Epistemic Delta: Expected change in knowledge vectors from completing a lesson
- Knowledge Graph: Relationships between lessons, skills, and domains
- Learning Path: Ordered sequence of lessons to achieve a goal

4-Layer Architecture:
- HOT: In-memory graph (nanoseconds) - fast relationship queries
- WARM: SQLite (microseconds) - queryable metadata
- SEARCH: Qdrant (milliseconds) - semantic similarity
- COLD: YAML files (10ms) - full lesson content

Usage:
    from empirica.core.lessons import get_lesson_storage, Lesson

    storage = get_lesson_storage()

    # Create a lesson
    lesson = Lesson(...)
    result = storage.create_lesson(lesson)

    # Find lessons that improve a specific vector
    lessons = storage.search_lessons(improves_vector='know')

    # Get optimal learning path
    path = storage.get_learning_path(target_lesson_id, completed={})
"""

from .hot_cache import LessonHotCache, get_hot_cache, initialize_hot_cache
from .schema import (
    Correction,
    EpistemicDelta,
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    Lesson,
    LessonEpistemic,
    LessonPhase,
    LessonRelation,
    LessonStep,
    LessonValidation,
    Prerequisite,
    PrerequisiteType,
    RelationType,
    StepCriticality,
)
from .storage import LessonStorageManager, get_lesson_storage

__all__ = [
    "Correction",
    "EpistemicDelta",
    "KnowledgeGraphEdge",
    "KnowledgeGraphNode",
    # Schema
    "Lesson",
    "LessonEpistemic",
    # Hot Cache
    "LessonHotCache",
    "LessonPhase",
    "LessonRelation",
    "LessonStep",
    # Storage
    "LessonStorageManager",
    "LessonValidation",
    "Prerequisite",
    "PrerequisiteType",
    "RelationType",
    "StepCriticality",
    "get_hot_cache",
    "get_lesson_storage",
    "initialize_hot_cache",
]
