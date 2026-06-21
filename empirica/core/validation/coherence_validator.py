"""
Coherence Validator - Self-Check Before Handoff

AI validates its own work before handing off to next AI.

Validates:
1. Git diff matches plan (no scope creep)
2. Epistemic trajectory is coherent (learning makes sense)
3. Findings are honestly tagged (not overstated)

Phase 3 Component: Ensures handoff context is valid
"""

import logging
from typing import Any

from .validation_utils import analyze_epistemic_trajectory, get_git_diff_summary

logger = logging.getLogger(__name__)


class CoherenceValidator:
    """Validates AI's work coherence before handing off"""

    def __init__(self, session_id: str, ai_id: str = "unknown"):
        """
        Initialize validator.

        Args:
            session_id: Current session ID
            ai_id: AI identifier
        """
        self.session_id = session_id
        self.ai_id = ai_id

    def validate_before_handoff(
        self,
        preflight_vectors: dict[str, float],
        postflight_vectors: dict[str, float],
        preflight_plan: dict[str, Any] | None = None,
        findings: list[dict] | None = None,
        unknowns: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Validate: can I hand off, or do I need to investigate more?

        Args:
            preflight_vectors: Assessment at start of phase
            postflight_vectors: Current epistemic assessment
            preflight_plan: My original plan for what I'd do
            findings: Tagged findings I discovered
            unknowns: Tagged unknowns remaining

        Returns:
            Validation result:
            {
                "coherent": bool,
                "checks": {
                    "scope_match": bool/str,
                    "trajectory": bool/str,
                    "findings_honest": bool/str
                },
                "recommendation": str - "handoff_ok" / "reenter_check" / "reassess",
                "concerns": [str] - list of issues found
            }
        """
        concerns = []
        checks = {}

        # 1. SCOPE CHECK: Did I do what I planned?
        scope_result = self._check_scope_match(preflight_plan)
        checks["scope_match"] = scope_result["valid"]
        if not scope_result["valid"]:
            concerns.append(f"Scope mismatch: {scope_result['reason']}")

        # 2. TRAJECTORY CHECK: Is my learning coherent?
        trajectory_result = self._check_trajectory(preflight_vectors, postflight_vectors)
        checks["trajectory"] = trajectory_result["coherent"]
        if not trajectory_result["coherent"]:
            concerns.append(f"Incoherent trajectory: {trajectory_result['concern']}")

        # 3. FINDINGS CHECK: Are my findings honest?
        if findings:
            findings_result = self._check_findings_honesty(findings, postflight_vectors)
            checks["findings_honest"] = findings_result["honest"]
            if not findings_result["honest"]:
                concerns.append(f"Findings concern: {findings_result['issue']}")

        # Determine recommendation
        if concerns:
            recommendation = "reenter_check"  # Go back to CHECK phase
            coherent = False
        else:
            recommendation = "handoff_ok"
            coherent = True

        return {
            "coherent": coherent,
            "checks": checks,
            "recommendation": recommendation,
            "concerns": concerns,
            "session_id": self.session_id,
            "ai_id": self.ai_id,
            "message": self._format_message(coherent, recommendation, concerns),
        }

    def _check_scope_match(self, preflight_plan: dict | None) -> dict[str, Any]:
        """
        Check: Did I do what I planned?

        Args:
            preflight_plan: Original plan (scope estimate, what I'd work on)

        Returns:
            {
                "valid": bool,
                "reason": str - explanation
            }
        """
        if not preflight_plan:
            # No plan provided = assume okay (not an error condition)
            return {"valid": True, "reason": "no_plan_to_validate"}

        planned_scope = preflight_plan.get("scope_estimate", "medium")

        # Get actual work done
        actual_diff = get_git_diff_summary()

        if actual_diff.get("error"):
            # Can't validate without git diff
            return {"valid": False, "reason": f"Cannot validate git state: {actual_diff['error']}"}

        actual_scope = actual_diff.get("scope_estimate", "unknown")

        # Map scope names to rough sizes
        scope_sizes = {"small": 1, "medium": 2, "large": 3}
        planned_size = scope_sizes.get(planned_scope, 2)
        actual_size = scope_sizes.get(actual_scope, 2)

        # Allow one level of variance (e.g., planned small, did medium is okay)
        # But not large divergence
        if abs(actual_size - planned_size) > 1:
            return {"valid": False, "reason": f"Scope creep: planned {planned_scope}, did {actual_scope}"}

        return {"valid": True, "reason": "scope_matches"}

    def _check_trajectory(
        self, preflight_vectors: dict[str, float], postflight_vectors: dict[str, float]
    ) -> dict[str, Any]:
        """
        Check: Is my learning trajectory coherent?

        Args:
            preflight_vectors: Starting assessment
            postflight_vectors: Current assessment

        Returns:
            {
                "coherent": bool,
                "pattern": str,
                "concern": Optional[str]
            }
        """
        result = analyze_epistemic_trajectory(preflight_vectors, postflight_vectors)

        return {"coherent": result["coherent"], "pattern": result["pattern"], "concern": result.get("concern", "")}

    def _check_findings_honesty(self, findings: list[dict], postflight_vectors: dict[str, float]) -> dict[str, Any]:
        """
        Check: Are my findings honest? (Not overstating confidence)

        Args:
            findings: Tagged findings
            postflight_vectors: My assessment (know, clarity, etc.)

        Returns:
            {
                "honest": bool,
                "issue": Optional[str] - explanation if not honest
            }
        """
        if not findings:
            return {"honest": True, "issue": None}

        my_know = postflight_vectors.get("know", 0.5)
        my_clarity = postflight_vectors.get("clarity", 0.5)

        # Check 1: If I have low clarity, I shouldn't have many high-certainty findings
        high_certainty_findings = sum(1 for f in findings if f.get("certainty", 0.5) > 0.8)

        if my_clarity < 0.5 and high_certainty_findings > len(findings) * 0.5:
            return {
                "honest": False,
                "issue": f"High certainty findings ({high_certainty_findings}) but low clarity ({my_clarity})",
            }

        # Check 2: If knowledge is low, findings should be tentative
        if my_know < 0.4 and high_certainty_findings > 2:
            return {"honest": False, "issue": f"High certainty findings but low knowledge ({my_know})"}

        return {"honest": True, "issue": None}

    def _format_message(self, coherent: bool, recommendation: str, concerns: list[str]) -> str:
        """Format human-readable validation message"""
        if coherent:
            return "✅ Coherence check PASSED. Ready to hand off."
        else:
            concerns_text = "\n   ".join(concerns)
            return f"⚠️ Coherence check FAILED:\n   {concerns_text}\n   Recommendation: {recommendation}"
