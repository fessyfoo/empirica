"""Pure math: confidence, phase composite, work-phase derivation.

These have no color, no I/O, and no dependency on the rendering
backend. Same numbers feed both ANSI (CC plugin) and Rich (chat).
"""

from __future__ import annotations


def calculate_confidence(vectors: dict | None) -> float:
    """Weighted confidence score from epistemic vectors.

    Weights (lifted verbatim from CC's statusline_empirica.py so the
    two surfaces agree on what 'confidence' means):
      - know: 40%       (how much we understand)
      - 1 - uncertainty: 30%   (inverse of doubt)
      - context: 20%    (how well we understand the situation)
      - completion: 10% (how much is done)

    Returns:
        Float in [0.0, 1.0]. Empty/None vectors → 0.0.
    """
    if not vectors:
        return 0.0
    know = vectors.get("know", 0.5)
    uncertainty = vectors.get("uncertainty", 0.5)
    context = vectors.get("context", 0.5)
    completion = vectors.get("completion", 0.0)
    confidence = 0.40 * know + 0.30 * (1.0 - uncertainty) + 0.20 * context + 0.10 * completion
    return max(0.0, min(1.0, confidence))


def calculate_phase_composite(vectors: dict | None, phase: str) -> float:
    """Average of phase-relevant vectors, in [0.0, 1.0].

    Phases:
      - 'check': readiness gate — averages know, context, clarity,
        coherence, signal, density (the 'are we ready?' indicators)
      - 'noetic': investigation — averages clarity, coherence,
        signal, density (the 'are we learning?' indicators)
      - anything else (treated as 'praxic'): execution — averages
        state, change, completion, impact (the 'are we shipping?'
        indicators)

    None values in the dict are skipped (only present keys count).
    """
    if not vectors:
        return 0.0
    if phase == "check":
        keys = ["know", "context", "clarity", "coherence", "signal", "density"]
    elif phase == "noetic":
        keys = ["clarity", "coherence", "signal", "density"]
    else:
        keys = ["state", "change", "completion", "impact"]
    values = [vectors.get(k, 0.0) for k in keys if vectors.get(k) is not None]
    return sum(values) / len(values) if values else 0.0


def determine_work_phase(phase: str | None, gate_decision: str | None = None) -> str:
    """Map (transaction phase, CHECK gate decision) → 'noetic' | 'praxic'.

    Rules (matching CC's statusline_empirica.py):
      - PREFLIGHT → noetic (just opened, investigating)
      - CHECK + proceed → praxic (transitioning to action)
      - CHECK without proceed (or 'investigate') → noetic
      - POSTFLIGHT → praxic (work completed)
      - None / unknown → noetic (default to investigating)
    """
    if not phase:
        return "noetic"
    if phase == "PREFLIGHT":
        return "noetic"
    if phase == "CHECK":
        return "praxic" if gate_decision == "proceed" else "noetic"
    if phase == "POSTFLIGHT":
        return "praxic"
    return "noetic"
