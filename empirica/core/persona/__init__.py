"""
Empirica Persona System - Epistemic Vector Profiles

Personas are defined as epistemic vector configurations:
- Priors: Starting knowledge/confidence in domain
- Thresholds: Gates for uncertainty, confidence, signal quality
- Weights: Balance between foundation/comprehension/execution/engagement
- Focus domains: Semantic tags for domain expertise

Personas are stored as JSON files in .empirica/personas/ and loaded
via system prompts or MCP configuration. No runtime harness needed.

Components:
- PersonaProfile: Persona configuration schema
- PersonaManager: Create, load, validate personas from JSON files
- Validation: Schema validation for persona profiles

Usage:
    from empirica.core.persona import PersonaManager

    # Load security expert persona
    manager = PersonaManager()
    persona = manager.load_persona("security_expert")

    # Use persona config in PREFLIGHT vectors
    priors = persona.epistemic_config.priors
"""

from .persona_manager import PersonaManager
from .persona_profile import (
    CapabilitiesConfig,
    EpistemicConfig,
    PersonaMetadata,
    PersonaProfile,
    SentinelConfig,
    SigningIdentityConfig,
)
from .validation import ValidationError, validate_persona_profile

__all__ = [
    "CapabilitiesConfig",
    "EpistemicConfig",
    # Management
    "PersonaManager",
    "PersonaMetadata",
    # Core
    "PersonaProfile",
    "SentinelConfig",
    "SigningIdentityConfig",
    "ValidationError",
    "validate_persona_profile",
]

__version__ = "4.0.0"  # Phase 4 - Simplified (harness/sentinel removed)
