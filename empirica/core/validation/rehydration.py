"""
Epistemic Rehydration - Context Inheritance on Handoff

Next AI reads previous AI's findings/unknowns and auto-calibrates PREFLIGHT.

Enables:
1. Understanding of what previous AI learned
2. Awareness of remaining unknowns
3. Auto-calibration of starting confidence
4. Continuity of epistemic state across handoffs

Phase 3 Component: Makes handoffs coherent through mutual understanding
"""

import logging
from typing import Any

from .validation_utils import calculate_understanding_ratio, estimate_rehydration_boost

logger = logging.getLogger(__name__)


class EpistemicRehydration:
    """Next AI rehydrates context from previous checkpoint"""

    def __init__(self, session_id: str, ai_id: str = "unknown"):
        """
        Initialize rehydration.

        Args:
            session_id: Session ID
            ai_id: Next AI's identifier
        """
        self.session_id = session_id
        self.ai_id = ai_id

    def rehydrate_from_checkpoint(
        self, checkpoint_data: dict[str, Any], my_knowledge_assessment: dict[str, float]
    ) -> dict[str, Any]:
        """
        I'm resuming from a checkpoint. What should I understand before starting?

        Returns calibration guidance for PREFLIGHT:
        - Which findings am I inheriting?
        - Which unknowns should I be aware of?
        - Should I adjust my starting confidence?

        Args:
            checkpoint_data: Previous AI's checkpoint (with epistemic_tags)
            my_knowledge_assessment: My honest assessment of my knowledge

        Returns:
            Rehydration guidance:
            {
                "inherited_findings": [finding_dict],
                "inherited_unknowns": [unknown_dict],
                "understanding_ratio": float,
                "confidence_adjustment": float,
                "recommended_preflight_know": float,
                "warnings": [str],
                "ready_to_proceed": bool,
                "message": str
            }
        """
        epistemic_tags = checkpoint_data.get("epistemic_tags", {})

        findings = epistemic_tags.get("findings", [])
        unknowns = epistemic_tags.get("unknowns", [])
        deadends = epistemic_tags.get("deadends", [])

        my_know = my_knowledge_assessment.get("know", 0.5)

        # 1. Understanding check
        understanding_ratio = calculate_understanding_ratio(findings, my_knowledge_assessment)

        # 2. Confidence adjustment
        confidence_boost = estimate_rehydration_boost(findings, unknowns, my_know)
        recommended_know = min(my_know + confidence_boost, 0.95)

        # 3. Warnings
        warnings = self._identify_rehydration_warnings(
            findings, unknowns, deadends, understanding_ratio, my_knowledge_assessment
        )

        # 4. Ready to proceed?
        ready = understanding_ratio > 0.5  # Need to understand >50% of findings

        return {
            "inherited_findings": findings,
            "inherited_unknowns": unknowns,
            "inherited_deadends": deadends,
            "understanding_ratio": round(understanding_ratio, 2),
            "confidence_adjustment": round(confidence_boost, 3),
            "current_know": round(my_know, 2),
            "recommended_preflight_know": round(recommended_know, 2),
            "warnings": warnings,
            "ready_to_proceed": ready,
            "message": self._format_rehydration_message(understanding_ratio, confidence_boost, warnings, ready),
            "session_id": self.session_id,
            "ai_id": self.ai_id,
        }

    def _identify_rehydration_warnings(
        self,
        findings: list[dict],
        unknowns: list[dict],
        deadends: list[dict],
        understanding_ratio: float,
        my_knowledge: dict[str, float],
    ) -> list[str]:
        """
        Identify potential issues during rehydration.

        Args:
            findings: Previous AI's findings
            unknowns: Remaining unknowns
            deadends: Tried and failed approaches
            understanding_ratio: How much I understand
            my_knowledge: My knowledge assessment

        Returns:
            List of warning strings
        """
        warnings = []

        # Warning 1: Low understanding
        if understanding_ratio < 0.5:
            warnings.append(
                f"Low understanding of findings ({understanding_ratio * 100:.0f}%) - "
                "recommend additional investigation before proceeding"
            )

        # Warning 2: Many unknowns
        if len(unknowns) > 8:
            warnings.append(f"Many unknowns remaining ({len(unknowns)}) - problem may be more complex than appears")

        # Warning 3: Deadends without clear reason
        unexplained_deadends = [d for d in deadends if not d.get("blocker")]
        if unexplained_deadends:
            warnings.append(
                f"{len(unexplained_deadends)} deadends without clear blockers - understand why before retrying"
            )

        # Warning 4: My knowledge doesn't match findings complexity
        finding_count = len(findings)
        if finding_count > 5 and my_knowledge.get("know", 0.5) < 0.5:
            warnings.append(
                f"Many findings ({finding_count}) but low knowledge ({my_knowledge['know']}) - "
                "may need deeper investigation"
            )

        # Warning 5: Contradictory signals
        findings_confidence = sum(f.get("certainty", 0.5) for f in findings) / max(len(findings), 1)
        if findings_confidence < 0.6 and len(findings) > 3:
            warnings.append("Low confidence findings despite many discoveries - previous AI may have been uncertain")

        return warnings

    def _format_rehydration_message(
        self, understanding_ratio: float, confidence_boost: float, warnings: list[str], ready: bool
    ) -> str:
        """Format human-readable rehydration message"""
        msg = "\n📚 Epistemic Rehydration:\n"
        msg += f"   Understanding: {understanding_ratio * 100:.0f}%\n"
        msg += f"   Confidence boost: +{confidence_boost:.3f}\n"

        if warnings:
            msg += f"   ⚠️  Warnings ({len(warnings)}):\n"
            for w in warnings:
                msg += f"      - {w}\n"

        if ready:
            msg += "   ✅ Ready to proceed with updated context\n"
        else:
            msg += "   ⚠️  Low understanding - recommend additional investigation\n"

        return msg

    def calculate_adjusted_preflight(
        self, checkpoint_data: dict[str, Any], my_base_assessment: dict[str, float]
    ) -> dict[str, float]:
        """
        Calculate adjusted PREFLIGHT vectors based on rehydration.

        Takes my honest assessment and adjusts based on inherited context.

        Args:
            checkpoint_data: Previous checkpoint with epistemic_tags
            my_base_assessment: My original PREFLIGHT assessment

        Returns:
            Adjusted assessment dict with same keys as input
        """
        rehydration = self.rehydrate_from_checkpoint(checkpoint_data, my_base_assessment)

        adjusted = my_base_assessment.copy()

        # Only adjust 'know' if we have good understanding
        if rehydration["understanding_ratio"] > 0.6:
            adjusted["know"] = rehydration["recommended_preflight_know"]

        # Slightly increase uncertainty if many unknowns
        unknowns_count = len(rehydration["inherited_unknowns"])
        if unknowns_count > 5:
            current_uncertainty = adjusted.get("uncertainty", 0.5)
            # Increase uncertainty awareness
            adjusted["uncertainty"] = min(current_uncertainty + 0.1, 0.9)

        # Increase context awareness if we have findings
        findings_count = len(rehydration["inherited_findings"])
        if findings_count > 3:
            # We have context now
            adjusted["context"] = min(adjusted.get("context", 0.5) + 0.1, 0.9)

        return adjusted
