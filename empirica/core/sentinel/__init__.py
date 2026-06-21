"""
Sentinel Orchestration Layer

Domain-aware epistemic governance with:
- Persona selection via Qdrant similarity
- Auto-spawning of parallel epistemic agents
- Domain profile compliance gates
- CHECK phase enhancement

Usage:
    from empirica.core.sentinel import Sentinel, DecisionLogic

    sentinel = Sentinel(session_id=session_id)
    sentinel.load_domain_profile("healthcare")

    # Auto-orchestrate a task
    result = sentinel.orchestrate(
        task="Analyze authentication vulnerabilities",
        max_agents=3
    )
"""

from .decision_logic import DecisionLogic, PersonaMatch
from .orchestrator import (
    ComplianceGate,
    DomainProfile,
    EpistemicLoopTracker,
    GateAction,
    LoopMode,
    LoopRecord,
    MergeStrategy,
    OrchestrationResult,
    Sentinel,
)

__all__ = [
    "ComplianceGate",
    "DecisionLogic",
    "DomainProfile",
    "EpistemicLoopTracker",
    "GateAction",
    "LoopMode",
    "LoopRecord",
    "MergeStrategy",
    "OrchestrationResult",
    "PersonaMatch",
    "Sentinel",
]
