"""
Workflow Pattern Mining — detect repeated tool sequences across transactions.

Architecture:
- Traces recorded by sentinel-gate.py in hook_counters['tool_trace']
- POSTFLIGHT archives traces to sessions.db (reflex_data.tool_trace)
- This module runs detection on archived traces
- Suggestion engine correlates patterns with epistemic outcomes
- Patterns surfaced as findings via CLI or at session start

Algorithm (inspired by Zoku, adapted for Empirica):
1. Extract contiguous subsequences (length 3-10)
2. Normalize: tool_name + phase only (ignore targets for matching)
3. Count distinct transactions per subsequence
4. Filter: appears in 2+ transactions
5. Remove strict subsets of longer patterns
6. Rank by frequency then length

Suggestion Engine (Layer 3 — epistemic-correlated):
- Joins tool_trace with PREFLIGHT/POSTFLIGHT vectors per transaction
- Correlates patterns with outcomes (completion, calibration score)
- Identifies patterns that appear in successful vs unsuccessful transactions
- Generates actionable suggestions: "when uncertain, do X before Y"
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Subsequence length bounds
MIN_SEQ_LEN = 3
MAX_SEQ_LEN = 10
# Minimum transaction count for a pattern to be reported
MIN_FREQUENCY = 2


@dataclass
class WorkflowPattern:
    """A detected repeated workflow pattern."""

    sequence: list[str]  # e.g. ["Read(n)", "Grep(n)", "Edit(p)", "Bash(p)"]
    frequency: int  # Number of distinct transactions
    transaction_ids: list[str]  # Which transactions exhibited this
    avg_position: float = 0.0  # Average position in transaction (0.0=start, 1.0=end)
    example_targets: list[str] = field(default_factory=list)  # Example file/cmd targets

    @property
    def signature(self) -> str:
        """Human-readable signature like 'Read→Grep→Edit→Bash(test)'."""
        return " → ".join(self.sequence)

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "signature": self.signature,
            "frequency": self.frequency,
            "transaction_ids": self.transaction_ids[:5],
            "avg_position": round(self.avg_position, 2),
            "example_targets": self.example_targets[:3],
        }


def normalize_trace(trace: list[list[str]]) -> list[str]:
    """Normalize a tool trace to comparable tokens.

    Input: [["Read", "server.py", "n"], ["Grep", "pattern", "n"], ...]
    Output: ["Read(n)", "Grep(n)", ...]

    Targets are dropped for matching (Read(n) matches Read(n) regardless of file).
    """
    tokens = []
    for entry in trace:
        if not entry or len(entry) < 3:
            continue
        tool_name = entry[0]
        phase = entry[2] if len(entry) > 2 else "n"
        tokens.append(f"{tool_name}({phase})")
    return tokens


def extract_subsequences(
    tokens: list[str], min_len: int = MIN_SEQ_LEN, max_len: int = MAX_SEQ_LEN
) -> list[tuple[str, ...]]:
    """Extract all contiguous subsequences of the given length range."""
    subsequences = []
    for length in range(min_len, min(max_len + 1, len(tokens) + 1)):
        for start in range(len(tokens) - length + 1):
            subseq = tuple(tokens[start : start + length])
            subsequences.append(subseq)
    return subsequences


def remove_subsets(patterns: dict[tuple[str, ...], set[str]]) -> dict[tuple[str, ...], set[str]]:
    """Remove patterns that are strict subsets of longer patterns with same frequency."""
    to_remove = set()
    sorted_patterns = sorted(patterns.keys(), key=len, reverse=True)

    for i, longer in enumerate(sorted_patterns):
        for shorter in sorted_patterns[i + 1 :]:
            if shorter in to_remove:
                continue
            # Check if shorter is a contiguous subsequence of longer
            longer_str = " ".join(longer)
            shorter_str = " ".join(shorter)
            if shorter_str in longer_str:
                # Only remove if frequency is same or lower
                if len(patterns[shorter]) <= len(patterns[longer]):
                    to_remove.add(shorter)

    return {k: v for k, v in patterns.items() if k not in to_remove}


def detect_patterns(traces: dict[str, list[list[str]]], min_frequency: int = MIN_FREQUENCY) -> list[WorkflowPattern]:
    """Detect repeated workflow patterns across transactions.

    Args:
        traces: {transaction_id: tool_trace} where tool_trace is
                [[tool_name, target, phase], ...]
        min_frequency: Minimum number of distinct transactions for a pattern

    Returns:
        List of WorkflowPattern sorted by frequency (desc) then length (desc)
    """
    if len(traces) < min_frequency:
        return []

    # Step 1: Normalize all traces
    normalized: dict[str, list[str]] = {}
    raw_traces: dict[str, list[list[str]]] = {}
    for tx_id, trace in traces.items():
        tokens = normalize_trace(trace)
        if len(tokens) >= MIN_SEQ_LEN:
            normalized[tx_id] = tokens
            raw_traces[tx_id] = trace

    if len(normalized) < min_frequency:
        return []

    # Step 2: Extract subsequences per transaction, count distinct transactions
    pattern_txs: dict[tuple[str, ...], set[str]] = {}
    pattern_positions: dict[tuple[str, ...], list[float]] = {}

    for tx_id, tokens in normalized.items():
        seen_in_tx: set[tuple[str, ...]] = set()
        subseqs = extract_subsequences(tokens)
        for subseq in subseqs:
            if subseq not in seen_in_tx:
                seen_in_tx.add(subseq)
                pattern_txs.setdefault(subseq, set()).add(tx_id)
                # Track position (normalized 0-1)
                idx = tokens.index(subseq[0]) if subseq[0] in tokens else 0
                pos = idx / max(len(tokens) - 1, 1)
                pattern_positions.setdefault(subseq, []).append(pos)

    # Step 3: Filter by minimum frequency
    frequent = {seq: txs for seq, txs in pattern_txs.items() if len(txs) >= min_frequency}

    if not frequent:
        return []

    # Step 4: Remove strict subsets
    filtered = remove_subsets(frequent)

    # Step 5: Build WorkflowPattern objects
    patterns = []
    for seq, tx_ids in filtered.items():
        # Get example targets from the first matching transaction
        example_targets = []
        for tx_id in list(tx_ids)[:1]:
            raw = raw_traces.get(tx_id, [])
            for entry in raw:
                if len(entry) >= 2 and entry[1]:
                    example_targets.append(f"{entry[0]}:{entry[1]}")
                    if len(example_targets) >= 3:
                        break

        avg_pos = sum(pattern_positions.get(seq, [0])) / max(len(pattern_positions.get(seq, [1])), 1)

        patterns.append(
            WorkflowPattern(
                sequence=list(seq),
                frequency=len(tx_ids),
                transaction_ids=sorted(tx_ids),
                avg_position=avg_pos,
                example_targets=example_targets,
            )
        )

    # Step 6: Sort by frequency (desc), then length (desc)
    patterns.sort(key=lambda p: (-p.frequency, -len(p.sequence)))

    return patterns


def load_traces_from_db(db_path: str, limit: int = 50) -> dict[str, list[list[str]]]:
    """Load tool traces from sessions.db reflex_data.

    Traces are stored in POSTFLIGHT reflex_data as 'tool_trace' field
    (copied from hook_counters at POSTFLIGHT time).

    Args:
        db_path: Path to sessions.db
        limit: Maximum number of recent transactions to load

    Returns:
        {transaction_id: tool_trace}
    """
    import sqlite3

    traces = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT transaction_id, reflex_data
            FROM reflexes
            WHERE phase = 'POSTFLIGHT'
            AND reflex_data IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (limit,),
        ).fetchall()

        for row in rows:
            try:
                data = json.loads(row["reflex_data"])
                trace = data.get("tool_trace", [])
                tx_id = row["transaction_id"]
                if trace and tx_id:
                    traces[tx_id] = trace
            except (json.JSONDecodeError, TypeError):
                continue

        conn.close()
    except Exception as e:
        logger.warning(f"Failed to load traces: {e}")

    return traces


def format_patterns_human(patterns: list[WorkflowPattern], limit: int = 5) -> str:
    """Format patterns for human display."""
    if not patterns:
        return "No repeated workflow patterns detected yet."

    lines = [f"Detected {len(patterns)} workflow pattern(s):\n"]
    for i, p in enumerate(patterns[:limit]):
        lines.append(f"  {i + 1}. {p.signature}")
        lines.append(
            f"     Frequency: {p.frequency} transactions | "
            f"Position: {'early' if p.avg_position < 0.3 else 'mid' if p.avg_position < 0.7 else 'late'}"
        )
        if p.example_targets:
            lines.append(f"     Examples: {', '.join(p.example_targets)}")
        lines.append("")

    if len(patterns) > limit:
        lines.append(f"  ... and {len(patterns) - limit} more")

    return "\n".join(lines)


# =============================================================================
# Layer 3: Epistemic-Correlated Suggestions
# =============================================================================


@dataclass
class TransactionOutcome:
    """A transaction's trace + epistemic outcome for correlation."""

    transaction_id: str
    trace: list[list[str]]
    pre_know: float = 0.0
    pre_uncertainty: float = 0.0
    post_know: float = 0.0
    post_completion: float = 0.0
    calibration_score: float = 1.0  # Lower = better calibrated
    success: bool = False  # completion >= 0.7


@dataclass
class WorkflowSuggestion:
    """An actionable suggestion derived from pattern-outcome correlation."""

    suggestion: str
    evidence: str
    confidence: float  # 0.0-1.0 based on sample size and effect size
    pattern: str  # The pattern signature this relates to
    category: str  # "investigation", "verification", "artifact", "timing"

    def to_dict(self) -> dict:
        return {
            "suggestion": self.suggestion,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 2),
            "pattern": self.pattern,
            "category": self.category,
        }


def load_transaction_outcomes(db_path: str, limit: int = 50) -> list[TransactionOutcome]:
    """Load transactions with both traces and vectors for correlation.

    Joins PREFLIGHT and POSTFLIGHT reflexes by transaction_id to get
    the full picture: starting vectors + trace + ending vectors + calibration.
    """
    import sqlite3

    outcomes = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Get POSTFLIGHTs with traces
        rows = conn.execute(
            """
            SELECT r.transaction_id, r.reflex_data, r.session_id
            FROM reflexes r
            WHERE r.phase = 'POSTFLIGHT'
            AND r.reflex_data IS NOT NULL
            AND json_extract(r.reflex_data, '$.tool_trace') IS NOT NULL
            ORDER BY r.timestamp DESC
            LIMIT ?
        """,
            (limit,),
        ).fetchall()

        for row in rows:
            try:
                post_data = json.loads(row["reflex_data"])
                trace = post_data.get("tool_trace", [])
                if not trace:
                    continue

                tx_id = row["transaction_id"]

                # Get POSTFLIGHT vectors
                post_vectors = post_data.get("vectors", {})
                if isinstance(post_vectors, str):
                    post_vectors = json.loads(post_vectors)

                post_completion = float(post_vectors.get("completion", 0))
                post_know = float(post_vectors.get("know", 0))

                # Get calibration score if available
                cal_score = post_data.get("calibration_score", 1.0)
                if cal_score is None:
                    cal_score = 1.0

                # Get PREFLIGHT vectors for this transaction
                pre_row = conn.execute(
                    """
                    SELECT reflex_data FROM reflexes
                    WHERE transaction_id = ? AND phase = 'PREFLIGHT'
                    ORDER BY timestamp ASC LIMIT 1
                """,
                    (tx_id,),
                ).fetchone()

                pre_know = 0.0
                pre_uncertainty = 0.5
                if pre_row:
                    pre_data = json.loads(pre_row["reflex_data"])
                    pre_vectors = pre_data.get("vectors", {})
                    if isinstance(pre_vectors, str):
                        pre_vectors = json.loads(pre_vectors)
                    pre_know = float(pre_vectors.get("know", 0))
                    pre_uncertainty = float(pre_vectors.get("uncertainty", 0.5))

                outcomes.append(
                    TransactionOutcome(
                        transaction_id=tx_id,
                        trace=trace,
                        pre_know=pre_know,
                        pre_uncertainty=pre_uncertainty,
                        post_know=post_know,
                        post_completion=post_completion,
                        calibration_score=float(cal_score),
                        success=post_completion >= 0.7,
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        conn.close()
    except Exception as e:
        logger.warning(f"Failed to load transaction outcomes: {e}")

    return outcomes


def _noetic_before_praxic(trace: list[list[str]]) -> int:
    """Count noetic tools before first praxic tool."""
    count = 0
    for entry in trace:
        if len(entry) >= 3 and entry[2] == "p":
            break
        count += 1
    return count


def _has_grep_before_edit(trace: list[list[str]]) -> bool:
    """Check if any Grep appears before the first Edit."""
    seen_grep = False
    for entry in trace:
        if entry[0] == "Grep":
            seen_grep = True
        if entry[0] == "Edit":
            return seen_grep
    return False


_ARTIFACT_CMDS = {
    "empirica",
    "finding-log",
    "unknown-log",
    "deadend-log",
    "assumption-log",
    "decision-log",
    "mistake-log",
}


def _count_artifact_tools(trace: list[list[str]]) -> int:
    """Count empirica artifact CLI calls."""
    return sum(1 for e in trace if len(e) >= 2 and e[1] in _ARTIFACT_CMDS)


def _analyse_noetic_depth(successful: list, unsuccessful: list, total: int) -> WorkflowSuggestion | None:
    """Analysis 1: Do successful transactions investigate more before acting?"""
    if not successful or not unsuccessful:
        return None
    avg_s = sum(_noetic_before_praxic(o.trace) for o in successful) / len(successful)
    avg_f = sum(_noetic_before_praxic(o.trace) for o in unsuccessful) / max(len(unsuccessful), 1)
    if avg_s <= avg_f + 1.5:
        return None
    effect = avg_s - avg_f
    return WorkflowSuggestion(
        suggestion=f"Investigate more before acting — successful transactions average "
        f"{avg_s:.1f} noetic tools before first edit, unsuccessful average {avg_f:.1f}",
        evidence=f"Based on {len(successful)} successful and {len(unsuccessful)} unsuccessful transactions",
        confidence=min(0.9, 0.4 + (total / 50) * 0.3 + (effect / 10) * 0.2),
        pattern="noetic-depth-before-praxic",
        category="investigation",
    )


def _analyse_grep_before_edit(successful: list, unsuccessful: list, total: int) -> WorkflowSuggestion | None:
    """Analysis 2: Does searching before editing improve outcomes?"""
    if not successful or not unsuccessful:
        return None
    rate_s = sum(1 for o in successful if _has_grep_before_edit(o.trace)) / len(successful)
    rate_f = sum(1 for o in unsuccessful if _has_grep_before_edit(o.trace)) / max(len(unsuccessful), 1)
    if rate_s <= rate_f + 0.2:
        return None
    grep_s = sum(1 for o in successful if _has_grep_before_edit(o.trace))
    grep_f = sum(1 for o in unsuccessful if _has_grep_before_edit(o.trace))
    return WorkflowSuggestion(
        suggestion=f"Search before editing — {rate_s:.0%} of successful transactions "
        f"include Grep before first Edit vs {rate_f:.0%} of unsuccessful",
        evidence=f"Grep-before-Edit rate: {grep_s}/{len(successful)} successful, "
        f"{grep_f}/{len(unsuccessful)} unsuccessful",
        confidence=min(0.85, 0.3 + (total / 40) * 0.3 + (rate_s - rate_f) * 0.3),
        pattern="grep-before-edit",
        category="investigation",
    )


def _analyse_uncertainty_depth(outcomes: list, min_sample: int) -> WorkflowSuggestion | None:
    """Analysis 3: When uncertain, do longer transactions succeed more?"""
    uncertain = [o for o in outcomes if o.pre_uncertainty > 0.4]
    if len(uncertain) < min_sample:
        return None
    uc_success = [o for o in uncertain if o.success]
    uc_fail = [o for o in uncertain if not o.success]
    if not uc_success or not uc_fail:
        return None
    avg_s = sum(len(o.trace) for o in uc_success) / len(uc_success)
    avg_f = sum(len(o.trace) for o in uc_fail) / max(len(uc_fail), 1)
    if avg_s <= avg_f + 3:
        return None
    return WorkflowSuggestion(
        suggestion=f"When uncertain, take more steps — successful uncertain transactions "
        f"average {avg_s:.0f} tool calls vs {avg_f:.0f} for unsuccessful",
        evidence=f"Based on {len(uncertain)} transactions starting with uncertainty > 0.4",
        confidence=min(0.8, 0.3 + len(uncertain) / 30),
        pattern="uncertain-transaction-depth",
        category="timing",
    )


def _analyse_artifact_breadth(successful: list, unsuccessful: list, total: int) -> WorkflowSuggestion | None:
    """Analysis 4: Does logging more artifacts correlate with success?"""
    if not successful or not unsuccessful:
        return None
    avg_s = sum(_count_artifact_tools(o.trace) for o in successful) / len(successful)
    avg_f = sum(_count_artifact_tools(o.trace) for o in unsuccessful) / max(len(unsuccessful), 1)
    if avg_s <= avg_f + 0.5:
        return None
    return WorkflowSuggestion(
        suggestion=f"Log more artifacts — successful transactions average "
        f"{avg_s:.1f} epistemic logs vs {avg_f:.1f} in unsuccessful",
        evidence="Artifact types: findings, unknowns, dead-ends, assumptions, decisions",
        confidence=min(0.75, 0.3 + total / 40),
        pattern="artifact-breadth-correlation",
        category="artifact",
    )


def _analyse_calibration_correlation(outcomes: list) -> WorkflowSuggestion | None:
    """Analysis 5: Does investigation depth improve calibration?"""
    well = [o for o in outcomes if o.calibration_score < 0.3]
    poor = [o for o in outcomes if o.calibration_score >= 0.5]
    if len(well) < 2 or len(poor) < 2:
        return None
    avg_good = sum(_noetic_before_praxic(o.trace) for o in well) / len(well)
    avg_bad = sum(_noetic_before_praxic(o.trace) for o in poor) / len(poor)
    if avg_good <= avg_bad + 1:
        return None
    return WorkflowSuggestion(
        suggestion=f"More investigation improves calibration — well-calibrated transactions "
        f"average {avg_good:.1f} noetic tools vs {avg_bad:.1f} for poorly calibrated",
        evidence=f"Based on {len(well)} well-calibrated (score < 0.3) and "
        f"{len(poor)} poorly calibrated (score >= 0.5) transactions",
        confidence=min(0.85, 0.4 + len(outcomes) / 30),
        pattern="investigation-calibration-correlation",
        category="verification",
    )


def generate_suggestions(outcomes: list[TransactionOutcome], min_sample: int = 3) -> list[WorkflowSuggestion]:
    """Generate workflow suggestions by correlating patterns with outcomes."""
    if len(outcomes) < min_sample:
        return []

    successful = [o for o in outcomes if o.success]
    unsuccessful = [o for o in outcomes if not o.success]
    total = len(outcomes)

    # Run all analyses, collect non-None results
    analyses = [
        _analyse_noetic_depth(successful, unsuccessful, total),
        _analyse_grep_before_edit(successful, unsuccessful, total),
        _analyse_uncertainty_depth(outcomes, min_sample),
        _analyse_artifact_breadth(successful, unsuccessful, total),
        _analyse_calibration_correlation(outcomes),
    ]

    suggestions = [s for s in analyses if s is not None]
    suggestions.sort(key=lambda s: -s.confidence)
    return suggestions


def format_suggestions_human(suggestions: list[WorkflowSuggestion], limit: int = 5) -> str:
    """Format suggestions for human display."""
    if not suggestions:
        return "Not enough data for suggestions yet. Need 3+ transactions with traces."

    lines = [f"Workflow Suggestions ({len(suggestions)} found):\n"]
    for _i, s in enumerate(suggestions[:limit]):
        icon = {"investigation": "🔍", "verification": "✓", "artifact": "📝", "timing": "⏱"}.get(s.category, "💡")
        lines.append(f"  {icon} {s.suggestion}")
        lines.append(f"     Confidence: {s.confidence:.0%} | Evidence: {s.evidence}")
        lines.append("")

    return "\n".join(lines)
