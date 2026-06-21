"""
Canonical Epistemic Self-Assessment System

Provides genuine LLM-powered metacognitive self-assessment without heuristics or confabulation.

Core Components:
- reflex_frame: Canonical data structures (VectorState, EpistemicAssessment, ReflexFrame)
- reflex_logger: Removed (replaced by GitEnhancedReflexLogger)

NOTE: Goal orchestration moved to empirica/core/goals/ (explicit goals system with subtasks)

Key Principles:
1. Genuine reasoning: LLM self-assessment, not keyword matching
2. Temporal separation: Log to JSON, act on logs in next pass
3. Clear terminology: epistemic weights ≠ internal weights
4. ENGAGEMENT gate: ≥0.60 required before proceeding
5. Canonical weights: 35/25/25/15 (foundation/comprehension/execution/engagement)
"""

# OLD EpistemicAssessment and ReflexFrame removed - use EpistemicAssessmentSchema
# For backwards compatibility during migration, import from schemas
from empirica.core.schemas.epistemic_assessment import EpistemicAssessmentSchema

# Import centralized thresholds
from ..thresholds import CRITICAL_THRESHOLDS, ENGAGEMENT_THRESHOLD
from .reflex_frame import CANONICAL_WEIGHTS, Action, VectorState

# Alias for backwards compatibility (will be removed after all code is updated)
EpistemicAssessment = EpistemicAssessmentSchema

# reflex_logger removed - use GitEnhancedReflexLogger instead

from .checkpoint_storage import CheckpointStorage
from .git_enhanced_reflex_logger import GitEnhancedReflexLogger
from .git_notes_storage import GitNotesStorage
from .git_state_capture import GitStateCapture

__all__ = [
    # Constants
    "CANONICAL_WEIGHTS",
    "CRITICAL_THRESHOLDS",
    "ENGAGEMENT_THRESHOLD",
    "Action",
    "CheckpointStorage",
    "EpistemicAssessment",  # Alias for EpistemicAssessmentSchema (backwards compat)
    # NEW schema (main export)
    "EpistemicAssessmentSchema",
    # Git-enhanced checkpoint system (refactored)
    "GitEnhancedReflexLogger",
    "GitNotesStorage",
    "GitStateCapture",
    # Data Structures
    "VectorState",
]
