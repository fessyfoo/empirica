"""Per-type half-lives and recency-decay for circle 1 (active state).

Decay is a tiebreaker WITHIN circle 1 only. Circles 2 and 3 do not use
recency. See PROPOSAL_BOOTSTRAP_AGGREGATOR.md → "Surfacing model" for
the rules each circle follows.
"""

from __future__ import annotations

import math
import time
from typing import Final

# Per-type half-lives in hours. ∞ means "no decay" — return 1.0 always.
# Values picked to match the relevance horizon of each artifact type:
#   - In-progress goals/subtasks don't decay (status > age).
#   - Findings: 30d default working memory.
#   - Recent decisions (no outcome): 30d (treat like findings until dust settles).
#   - Dead-ends / mistakes: 14d (failed approaches go stale fast as code shifts).
TYPE_HALF_LIFE_HOURS: Final[dict[str, float]] = {
    "finding": 30 * 24,
    "decision_recent": 30 * 24,  # decision with no outcome recorded yet
    "dead_end": 14 * 24,
    "mistake": 14 * 24,
    "goal_open": math.inf,  # in-progress / planned: status > age
    "subtask_open": math.inf,
}

# Type-confidence multipliers — kept consistent with epistemic_summarizer.py
# Findings most actionable; questions inherently uncertain.
TYPE_CONFIDENCE: Final[dict[str, float]] = {
    "finding": 0.85,
    "decision": 0.80,
    "dead_end": 0.75,
    "mistake": 0.70,
    "goal": 0.90,
    "subtask": 0.65,
    "unknown": 0.60,
    "assumption": 0.55,
    "source": 0.85,
}


def recency_decay(created_timestamp: float | str | None, half_life_hours: float) -> float:
    """Compute exponential recency decay weight in [0, 1].

    half_life_hours = math.inf → returns 1.0 (no decay).
    Missing timestamp → returns 1.0 (assume fresh; conservative).
    String timestamps parsed as ISO 8601.
    """
    if math.isinf(half_life_hours):
        return 1.0
    if created_timestamp is None:
        return 1.0

    if isinstance(created_timestamp, str):
        try:
            from datetime import datetime

            ts = datetime.fromisoformat(created_timestamp.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            return 1.0
    else:
        ts = float(created_timestamp)

    age_hours = (time.time() - ts) / 3600
    if age_hours <= 0:
        return 1.0
    decay_constant = math.log(2) / half_life_hours
    return math.exp(-decay_constant * age_hours)


def circle_1_weight(impact: float | None, type_key: str, created_timestamp: float | str | None) -> float:
    """Score for circle 1 (active state): impact × type_confidence × recency."""
    impact_val = impact if impact is not None else 0.5
    type_conf = TYPE_CONFIDENCE.get(type_key.split("_")[0], 0.5)
    half_life = TYPE_HALF_LIFE_HOURS.get(type_key, 30 * 24)
    return round(impact_val * type_conf * recency_decay(created_timestamp, half_life), 3)


def circle_2_weight(impact: float | None, type_key: str) -> float:
    """Score for circle 2 (persistent reference): impact × type_confidence, NO recency."""
    impact_val = impact if impact is not None else 0.5
    type_conf = TYPE_CONFIDENCE.get(type_key, 0.5)
    return round(impact_val * type_conf, 3)
