"""
Validation Utilities - Helper functions for epistemic validation

Provides common functionality for coherence checking, trajectory analysis,
and semantic understanding validation.
"""

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def get_git_diff_summary() -> dict[str, Any]:
    """
    Get summary of git changes since last checkpoint.

    Returns:
        Dictionary with:
        - file_count: number of files modified
        - line_additions: total lines added
        - line_deletions: total lines deleted
        - scope_estimate: rough scope (small/medium/large)
    """
    try:
        # Get diff statistics
        result = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True, timeout=5)

        if result.returncode != 0:
            return {
                "file_count": 0,
                "line_additions": 0,
                "line_deletions": 0,
                "scope_estimate": "unknown",
                "error": "git diff failed",
            }

        lines = result.stdout.strip().split("\n")
        file_count = len([l for l in lines if l and "+" in l])

        # Parse additions/deletions
        total_additions = 0
        total_deletions = 0

        for line in lines:
            if "+" in line or "-" in line:
                parts = line.split()
                for _i, part in enumerate(parts):
                    if "+" in part:
                        try:
                            total_additions += int(part.replace("+", ""))
                        except Exception:  # noqa: S110 — non-numeric diff stat; skip
                            pass
                    if "-" in part:
                        try:
                            total_deletions += int(part.replace("-", ""))
                        except Exception:  # noqa: S110 — non-numeric diff stat; skip
                            pass

        # Estimate scope
        total_changes = total_additions + total_deletions
        if total_changes < 100:
            scope = "small"
        elif total_changes < 500:
            scope = "medium"
        else:
            scope = "large"

        return {
            "file_count": file_count,
            "line_additions": total_additions,
            "line_deletions": total_deletions,
            "scope_estimate": scope,
            "error": None,
        }

    except Exception as e:
        logger.warning(f"Failed to get git diff: {e}")
        return {"file_count": 0, "line_additions": 0, "line_deletions": 0, "scope_estimate": "unknown", "error": str(e)}


def analyze_epistemic_trajectory(
    preflight_vectors: dict[str, float], postflight_vectors: dict[str, float]
) -> dict[str, Any]:
    """
    Analyze if epistemic trajectory is coherent.

    Coherent patterns:
    - KNOW↑ + CLARITY↑ + UNCERTAINTY↓ = learning happened
    - KNOW↓ + CLARITY↓ + UNCERTAINTY↑ = discovered complexity
    - KNOW→ + CLARITY→ = no learning (possible issue)

    Args:
        preflight_vectors: Assessment at start
        postflight_vectors: Assessment at end

    Returns:
        Dictionary with:
        - coherent: bool
        - pattern: learning/complexity_discovery/stagnation
        - deltas: changes in each vector
        - concern: optional warning message
    """
    deltas = {}
    for key in preflight_vectors:
        deltas[key] = postflight_vectors.get(key, 0.5) - preflight_vectors.get(key, 0.5)

    know_delta = deltas.get("know", 0)
    clarity_delta = deltas.get("clarity", 0)
    uncertainty_delta = deltas.get("uncertainty", 0)

    # Pattern detection
    if abs(know_delta) < 0.05 and abs(clarity_delta) < 0.05:
        pattern = "stagnation"
        coherent = False
        concern = "No epistemic change detected - verify work actually happened"

    elif know_delta > 0.1 and clarity_delta > 0.1 and uncertainty_delta < -0.1:
        pattern = "learning"
        coherent = True
        concern = None

    elif know_delta < -0.1 and clarity_delta < -0.1 and uncertainty_delta > 0.1:
        pattern = "complexity_discovery"
        coherent = True
        concern = None

    elif know_delta > 0.05 and uncertainty_delta > 0.1:
        pattern = "overconfidence"
        coherent = False
        concern = "Knowledge increased but uncertainty also increased - inconsistent"

    elif know_delta < -0.1 and uncertainty_delta < -0.1:
        pattern = "incoherent_improvement"
        coherent = False
        concern = "Knowledge decreased but uncertainty decreased - suspicious"

    else:
        pattern = "mixed"
        coherent = True
        concern = None

    return {"coherent": coherent, "pattern": pattern, "deltas": deltas, "concern": concern}


def understand_finding(finding: dict[str, Any], my_knowledge: dict[str, float]) -> bool:
    """
    Estimate if I (next AI) can understand a previous AI's finding.

    Simple heuristic: if the domain mentioned in finding matches my knowledge areas,
    assume I understand it. This is validated later during rehydration.

    Args:
        finding: Finding tag dict with 'key' and optional 'domain'
        my_knowledge: My assessed knowledge vectors

    Returns:
        bool - can I understand this finding?
    """
    try:
        finding.get("key", "")
        domain = finding.get("domain", "")

        # If very low knowledge overall, probably can't understand
        if my_knowledge.get("know", 0.5) < 0.3:
            return False

        # If finding is in a domain I'm weak in, might not understand
        if domain:
            domain_lower = domain.lower()
            # Very basic domain matching - this is just heuristic
            if any(word in domain_lower for word in ["database", "sql", "orm"]):
                return my_knowledge.get("context", 0.5) > 0.4

        # Default: assume I can understand if general knowledge is okay
        return my_knowledge.get("know", 0.5) > 0.5

    except Exception as e:
        logger.warning(f"Error in understand_finding: {e}")
        return True  # Optimistic default


def calculate_understanding_ratio(findings: list[dict], my_knowledge: dict[str, float]) -> float:
    """
    Calculate what percentage of findings I understand.

    Args:
        findings: List of finding dicts
        my_knowledge: My knowledge assessment

    Returns:
        float 0.0-1.0 - ratio of understood findings
    """
    if not findings:
        return 1.0  # No findings = understand everything

    understood = sum(1 for f in findings if understand_finding(f, my_knowledge))
    return understood / len(findings)


def estimate_rehydration_boost(findings: list[dict], unknowns: list[dict], my_know: float) -> float:
    """
    Estimate how much my knowledge should increase from rehydration.

    If I read findings/unknowns and understand them, my confidence
    should increase because context improved.

    Args:
        findings: List of findings from previous AI
        unknowns: List of unknowns remaining
        my_know: My current knowledge assessment

    Returns:
        float - boost to apply to PREFLIGHT know assessment
    """
    try:
        my_knowledge = {"know": my_know, "context": my_know * 0.9}

        understanding_ratio = calculate_understanding_ratio(findings, my_knowledge)

        # Boost is proportional to understanding ratio
        # Maximum boost is 0.15 (don't exceed reasonable calibration)
        boost = understanding_ratio * 0.15

        # Reduce boost if many unknowns remain (more caution needed)
        unknown_count = len(unknowns)
        if unknown_count > 10:
            boost *= 0.5
        elif unknown_count > 5:
            boost *= 0.7

        return min(boost, 0.15)

    except Exception as e:
        logger.warning(f"Error in estimate_rehydration_boost: {e}")
        return 0.0  # Conservative: no boost on error
