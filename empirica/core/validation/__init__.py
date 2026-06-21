"""
Epistemic Validation - Self-Healing Multi-AI System

This module provides the mechanisms for AIs to validate themselves and each other
using git-based coherence checks and semantic tag rehydration.

Phase 3 Components:
1. CoherenceValidator - validates AI's own work before handoff
2. EpistemicRehydration - next AI inherits context and auto-calibrates
3. HandoffValidator - validates incoming checkpoint quality
4. ValidationUtils - helper functions for all validators
"""

from .coherence_validator import CoherenceValidator
from .handoff_validator import HandoffValidator
from .rehydration import EpistemicRehydration

__all__ = [
    "CoherenceValidator",
    "EpistemicRehydration",
    "HandoffValidator",
]
