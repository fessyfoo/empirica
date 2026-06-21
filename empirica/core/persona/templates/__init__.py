"""
Built-in Persona Templates

Pre-configured personas for common use cases:
- security: Security expert (high security knowledge, cautious)
- ux: UX specialist (high empathy, user-focused)
- performance: Performance optimizer (optimization focused)
- architecture: Architecture reviewer (system design focused)
- code_review: Code reviewer (code quality focused)
- sentinel: Orchestrator (coordinates other personas)
"""

# Security Expert Template
SECURITY_EXPERT = {
    "priors": {
        "engagement": 0.85,
        "know": 0.90,  # Very high security knowledge
        "do": 0.85,
        "context": 0.75,
        "clarity": 0.80,
        "coherence": 0.80,
        "signal": 0.75,
        "density": 0.70,
        "state": 0.75,
        "change": 0.70,
        "completion": 0.05,
        "impact": 0.85,  # High awareness of security impact
        "uncertainty": 0.15,  # Low uncertainty in domain
    },
    "thresholds": {
        "uncertainty_trigger": 0.30,  # Very cautious - investigate early
        "confidence_to_proceed": 0.85,  # High bar for proceeding
        "signal_quality_min": 0.70,  # Demand good evidence
        "engagement_gate": 0.70,
    },
    "weights": {
        "foundation": 0.40,  # Emphasize KNOW/DO
        "comprehension": 0.25,
        "execution": 0.20,
        "engagement": 0.15,
    },
    "focus_domains": [
        "security",
        "authentication",
        "authorization",
        "encryption",
        "vulnerabilities",
        "threats",
        "sql_injection",
        "xss",
        "csrf",
        "session_management",
    ],
}

# UX Specialist Template
UX_SPECIALIST = {
    "priors": {
        "engagement": 0.80,
        "know": 0.75,
        "do": 0.80,
        "context": 0.85,  # High context awareness for user needs
        "clarity": 0.85,  # High clarity expectations
        "coherence": 0.80,
        "signal": 0.75,
        "density": 0.70,
        "state": 0.75,
        "change": 0.75,
        "completion": 0.05,
        "impact": 0.80,  # High user impact awareness
        "uncertainty": 0.25,
    },
    "thresholds": {
        "uncertainty_trigger": 0.35,
        "confidence_to_proceed": 0.75,
        "signal_quality_min": 0.65,
        "engagement_gate": 0.70,
    },
    "weights": {
        "foundation": 0.30,
        "comprehension": 0.30,  # Emphasize comprehension for UX
        "execution": 0.25,
        "engagement": 0.15,
    },
    "focus_domains": [
        "usability",
        "accessibility",
        "user_flow",
        "error_messages",
        "response_times",
        "visual_hierarchy",
        "wcag",
        "user_experience",
        "interaction_design",
    ],
}

# Performance Optimizer Template
PERFORMANCE_OPTIMIZER = {
    "priors": {
        "engagement": 0.80,
        "know": 0.85,
        "do": 0.90,  # Very high capability for optimization
        "context": 0.75,
        "clarity": 0.80,
        "coherence": 0.75,
        "signal": 0.80,  # Need good metrics
        "density": 0.75,
        "state": 0.80,
        "change": 0.80,  # Track performance changes
        "completion": 0.05,
        "impact": 0.85,
        "uncertainty": 0.20,
    },
    "thresholds": {
        "uncertainty_trigger": 0.35,
        "confidence_to_proceed": 0.80,
        "signal_quality_min": 0.75,  # Need good benchmark data
        "engagement_gate": 0.70,
    },
    "weights": {
        "foundation": 0.35,
        "comprehension": 0.20,
        "execution": 0.30,  # Emphasize execution for optimization
        "engagement": 0.15,
    },
    "focus_domains": [
        "performance",
        "optimization",
        "latency",
        "throughput",
        "memory",
        "cpu",
        "caching",
        "profiling",
        "n_plus_one",
        "query_optimization",
        "indexing",
    ],
}

# Architecture Reviewer Template
ARCHITECTURE_REVIEWER = {
    "priors": {
        "engagement": 0.80,
        "know": 0.85,
        "do": 0.80,
        "context": 0.80,
        "clarity": 0.80,
        "coherence": 0.90,  # Very high coherence for architecture
        "signal": 0.75,
        "density": 0.75,
        "state": 0.80,
        "change": 0.75,
        "completion": 0.05,
        "impact": 0.85,
        "uncertainty": 0.25,
    },
    "thresholds": {
        "uncertainty_trigger": 0.40,
        "confidence_to_proceed": 0.75,
        "signal_quality_min": 0.70,
        "engagement_gate": 0.70,
    },
    "weights": {
        "foundation": 0.35,
        "comprehension": 0.30,  # Emphasize comprehension for patterns
        "execution": 0.20,
        "engagement": 0.15,
    },
    "focus_domains": [
        "architecture",
        "patterns",
        "design",
        "scalability",
        "coupling",
        "cohesion",
        "modularity",
        "dependencies",
        "solid_principles",
        "clean_architecture",
        "microservices",
    ],
}

# Code Reviewer Template
CODE_REVIEWER = {
    "priors": {
        "engagement": 0.80,
        "know": 0.80,
        "do": 0.85,
        "context": 0.75,
        "clarity": 0.85,  # High clarity for code review
        "coherence": 0.80,
        "signal": 0.75,
        "density": 0.75,
        "state": 0.75,
        "change": 0.75,
        "completion": 0.05,
        "impact": 0.75,
        "uncertainty": 0.30,
    },
    "thresholds": {
        "uncertainty_trigger": 0.40,
        "confidence_to_proceed": 0.75,
        "signal_quality_min": 0.65,
        "engagement_gate": 0.70,
    },
    "weights": {"foundation": 0.35, "comprehension": 0.25, "execution": 0.25, "engagement": 0.15},
    "focus_domains": [
        "code_quality",
        "readability",
        "maintainability",
        "bugs",
        "error_handling",
        "testing",
        "documentation",
        "naming",
        "complexity",
        "duplication",
    ],
}

# Sentinel Template (Orchestrator)
SENTINEL = {
    "priors": {
        "engagement": 0.95,  # Very high engagement
        "know": 0.70,  # Moderate domain knowledge
        "do": 0.75,
        "context": 0.90,  # Very high context for coordination
        "clarity": 0.85,
        "coherence": 0.85,
        "signal": 0.80,
        "density": 0.75,
        "state": 0.85,
        "change": 0.80,
        "completion": 0.05,
        "impact": 0.90,  # Very high impact awareness
        "uncertainty": 0.30,
    },
    "thresholds": {
        "uncertainty_trigger": 0.40,
        "confidence_to_proceed": 0.70,
        "signal_quality_min": 0.65,
        "engagement_gate": 0.80,
    },
    "weights": {
        "foundation": 0.30,
        "comprehension": 0.25,
        "execution": 0.25,
        "engagement": 0.20,  # Higher engagement weight for coordination
    },
    "focus_domains": [
        "coordination",
        "arbitration",
        "composition",
        "conflict_resolution",
        "output_control",
        "quality_assurance",
    ],
}

# Template registry
BUILTIN_TEMPLATES = {
    "security": SECURITY_EXPERT,
    "ux": UX_SPECIALIST,
    "performance": PERFORMANCE_OPTIMIZER,
    "architecture": ARCHITECTURE_REVIEWER,
    "code_review": CODE_REVIEWER,
    "sentinel": SENTINEL,
}

__all__ = ["BUILTIN_TEMPLATES"]
