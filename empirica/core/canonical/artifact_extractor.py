"""
Artifact Extractor — Extract epistemic artifacts from conversation transcripts.

Takes ConversationTurn objects (from TranscriptParser or ClaudeAIParser) and
extracts structured Empirica artifacts: findings, decisions, dead-ends, mistakes,
unknowns.

Two extraction modes:
1. Rule-based (default): Pattern matching on conversation content
2. LLM-assisted (optional): Send conversation chunks to Claude API for classification

Extracted artifacts include a confidence score (0.0-1.0) indicating extraction quality:
- 0.9+: Explicit artifact (user said "I found X", "decided to Y")
- 0.6-0.8: Strong pattern match (tool chain fail→retry = dead-end)
- 0.3-0.5: Implicit/inferred (reasoning suggests a decision was made)

Architecture:
    ConversationTurn[] ──> ArtifactExtractor
                               ├── extract_findings()
                               ├── extract_decisions()
                               ├── extract_dead_ends()
                               ├── extract_mistakes()
                               ├── extract_unknowns()
                               └── extract_all() ──> ExtractionResult
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from empirica.core.canonical.transcript_parser import ConversationTurn

logger = logging.getLogger(__name__)


# --- Extracted Artifact Types ---


@dataclass
class ExtractedFinding:
    """A discovery or insight extracted from conversation."""

    finding: str
    impact: float = 0.5
    confidence: float = 0.5  # How confident is the extraction
    source_turn: int = 0
    source_text: str = ""  # The text that triggered extraction
    timestamp: str = ""


@dataclass
class ExtractedDecision:
    """A choice point extracted from conversation."""

    choice: str
    rationale: str = ""
    reversibility: str = "exploratory"
    confidence: float = 0.5
    source_turn: int = 0
    timestamp: str = ""


@dataclass
class ExtractedDeadEnd:
    """A failed approach extracted from conversation."""

    approach: str
    why_failed: str = ""
    confidence: float = 0.5
    source_turn: int = 0
    timestamp: str = ""


@dataclass
class ExtractedMistake:
    """An error or mistake extracted from conversation."""

    mistake: str
    why_wrong: str = ""
    prevention: str = ""
    confidence: float = 0.5
    source_turn: int = 0
    timestamp: str = ""


@dataclass
class ExtractedUnknown:
    """An open question or uncertainty extracted from conversation."""

    unknown: str
    confidence: float = 0.5
    source_turn: int = 0
    timestamp: str = ""


@dataclass
class ExtractionResult:
    """Complete extraction output from a session or conversation."""

    findings: list[ExtractedFinding] = field(default_factory=list)
    decisions: list[ExtractedDecision] = field(default_factory=list)
    dead_ends: list[ExtractedDeadEnd] = field(default_factory=list)
    mistakes: list[ExtractedMistake] = field(default_factory=list)
    unknowns: list[ExtractedUnknown] = field(default_factory=list)

    # Metadata
    source: str = ""  # "claude-code" or "claude-ai"
    session_id: str = ""
    turns_processed: int = 0
    extraction_timestamp: str = ""

    @property
    def total_artifacts(self) -> int:
        return len(self.findings) + len(self.decisions) + len(self.dead_ends) + len(self.mistakes) + len(self.unknowns)

    def summary(self) -> dict[str, Any]:
        return {
            "findings": len(self.findings),
            "decisions": len(self.decisions),
            "dead_ends": len(self.dead_ends),
            "mistakes": len(self.mistakes),
            "unknowns": len(self.unknowns),
            "total": self.total_artifacts,
            "turns_processed": self.turns_processed,
            "source": self.source,
        }

    def filter_by_confidence(self, min_confidence: float = 0.5) -> ExtractionResult:
        """Return a new ExtractionResult with only high-confidence artifacts."""
        return ExtractionResult(
            findings=[f for f in self.findings if f.confidence >= min_confidence],
            decisions=[d for d in self.decisions if d.confidence >= min_confidence],
            dead_ends=[d for d in self.dead_ends if d.confidence >= min_confidence],
            mistakes=[m for m in self.mistakes if m.confidence >= min_confidence],
            unknowns=[u for u in self.unknowns if u.confidence >= min_confidence],
            source=self.source,
            session_id=self.session_id,
            turns_processed=self.turns_processed,
            extraction_timestamp=self.extraction_timestamp,
        )


# --- Pattern Definitions ---


# Patterns that indicate explicit findings (high confidence)
FINDING_PATTERNS = [
    (re.compile(r"(?:I )?(?:found|discovered|noticed|realized|learned)\s+(?:that\s+)?(.{20,200})", re.I), 0.85),
    (re.compile(r"(?:key|important|interesting)\s+(?:finding|insight|observation):\s*(.{20,200})", re.I), 0.90),
    (re.compile(r"(?:it turns out|as it happens|surprisingly)\s+(.{20,200})", re.I), 0.75),
    (re.compile(r"(?:the root cause|the issue|the problem)\s+(?:is|was)\s+(.{20,200})", re.I), 0.85),
]

# Patterns that indicate decisions
DECISION_PATTERNS = [
    (
        re.compile(
            r"(?:I(?:'ll| will)?|let(?:'s| us)|we should)\s+(?:use|go with|choose|pick|implement|switch to)\s+(.{10,200})",
            re.I,
        ),
        0.80,
    ),
    (re.compile(r"(?:decided|decision|choosing)\s+(?:to\s+)?(.{10,200})", re.I), 0.85),
    (
        re.compile(
            r"(?:instead of|rather than)\s+(.{10,100}),?\s*(?:I(?:'ll| will)?|we(?:'ll| will)?|let(?:'s))\s+(.{10,200})",
            re.I,
        ),
        0.80,
    ),
]

# Patterns that indicate dead ends
DEAD_END_PATTERNS = [
    (re.compile(r"(?:that|this)\s+(?:\w+\s+)?(?:didn'?t|doesn'?t|won'?t)\s+work\b(.{0,200})", re.I), 0.75),
    (re.compile(r"(?:tried|attempted)\s+(.{10,150})\s+but\s+(.{10,200})", re.I), 0.80),
    (re.compile(r"(?:abandon|scrap|drop|revert)(?:ing|ed)?\s+(.{10,200})", re.I), 0.80),
    (re.compile(r"(?:dead end|wrong approach|wrong direction)\b(.{0,200})", re.I), 0.90),
]

# Patterns that indicate mistakes
MISTAKE_PATTERNS = [
    (re.compile(r"(?:my mistake|I made an error|I was wrong|oops|accidentally)\b(.{0,200})", re.I), 0.85),
    (re.compile(r"(?:should(?:n'?t)? have|forgot to|missed|overlooked)\s+(.{10,200})", re.I), 0.70),
    (re.compile(r"(?:that was|this was)\s+(?:a bug|an error|incorrect|wrong)\b(.{0,200})", re.I), 0.80),
]

# Patterns that indicate unknowns
UNKNOWN_PATTERNS = [
    (
        re.compile(r"(?:I(?:'m| am))?\s*(?:not sure|uncertain|unclear)\s+(?:about|whether|if|how)\s+(.{10,200})", re.I),
        0.80,
    ),
    (re.compile(r"(?:need to|should)\s+(?:investigate|figure out|understand|check|verify)\s+(.{10,200})", re.I), 0.75),
    (re.compile(r"(?:question|unknown|mystery):\s*(.{10,200})", re.I), 0.85),
    (re.compile(r"\?\s*$", re.M), 0.40),  # Questions (low confidence — many false positives)
]


# --- Extractor ---


class ArtifactExtractor:
    """Extract epistemic artifacts from conversation turns."""

    def __init__(
        self,
        min_confidence: float = 0.3,
        dedup_existing: set[str] | None = None,
    ):
        """
        Args:
            min_confidence: Minimum confidence to include an artifact.
            dedup_existing: Set of content hashes of existing artifacts (for deduplication).
        """
        self.min_confidence = min_confidence
        self._seen_hashes: set[str] = dedup_existing or set()

    def _content_hash(self, text: str) -> str:
        """Create a hash for deduplication."""
        normalized = re.sub(r"\s+", " ", text.lower().strip())
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _is_duplicate(self, text: str) -> bool:
        """Check if content has already been extracted."""
        h = self._content_hash(text)
        if h in self._seen_hashes:
            return True
        self._seen_hashes.add(h)
        return False

    def extract_findings(self, turns: list[ConversationTurn]) -> list[ExtractedFinding]:
        """Extract findings from conversation turns."""
        findings = []

        for turn in turns:
            # Check assistant text for finding patterns
            for text_source in [turn.assistant_text, turn.thinking]:
                if not text_source:
                    continue

                for pattern, base_confidence in FINDING_PATTERNS:
                    for match in pattern.finditer(text_source):
                        finding_text = match.group(1).strip() if match.lastindex else match.group(0).strip()
                        finding_text = self._clean_text(finding_text)

                        if len(finding_text) < 20 or self._is_duplicate(finding_text):
                            continue

                        # Boost confidence if in thinking (more deliberate)
                        confidence = min(1.0, base_confidence + (0.05 if text_source == turn.thinking else 0))
                        # Estimate impact from context
                        impact = self._estimate_impact(finding_text, turn)

                        findings.append(
                            ExtractedFinding(
                                finding=finding_text,
                                impact=impact,
                                confidence=confidence,
                                source_turn=turn.turn_index,
                                source_text=text_source[:200],
                                timestamp=turn.timestamp,
                            )
                        )

            # Tool chain patterns: successful tool use after investigation = finding
            for chain in turn.tool_chains:
                if chain.tool_name in ("Grep", "Glob", "Read") and chain.success:
                    if chain.result_content and len(chain.result_content) > 50:
                        # Don't extract raw tool results as findings — too noisy
                        pass

        return [f for f in findings if f.confidence >= self.min_confidence]

    def extract_decisions(self, turns: list[ConversationTurn]) -> list[ExtractedDecision]:
        """Extract decisions from conversation turns."""
        decisions = []

        for turn in turns:
            for text_source in [turn.assistant_text, turn.thinking]:
                if not text_source:
                    continue

                for pattern, base_confidence in DECISION_PATTERNS:
                    for match in pattern.finditer(text_source):
                        if match.lastindex and match.lastindex >= 2:
                            # Pattern with "instead of X, do Y"
                            choice = match.group(2).strip()
                            rationale = f"Instead of {match.group(1).strip()}"
                        elif match.lastindex:
                            choice = match.group(1).strip()
                            rationale = ""
                        else:
                            choice = match.group(0).strip()
                            rationale = ""

                        choice = self._clean_text(choice)
                        if len(choice) < 10 or self._is_duplicate(choice):
                            continue

                        confidence = min(1.0, base_confidence + (0.05 if text_source == turn.thinking else 0))

                        decisions.append(
                            ExtractedDecision(
                                choice=choice,
                                rationale=rationale,
                                confidence=confidence,
                                source_turn=turn.turn_index,
                                timestamp=turn.timestamp,
                            )
                        )

        return [d for d in decisions if d.confidence >= self.min_confidence]

    def extract_dead_ends(self, turns: list[ConversationTurn]) -> list[ExtractedDeadEnd]:
        """Extract dead ends from conversation turns."""
        dead_ends = []

        for turn in turns:
            # Pattern 1: Text-based dead end detection
            for text_source in [turn.assistant_text, turn.thinking]:
                if not text_source:
                    continue

                for pattern, base_confidence in DEAD_END_PATTERNS:
                    for match in pattern.finditer(text_source):
                        if match.lastindex and match.lastindex >= 2:
                            approach = match.group(1).strip()
                            why_failed = match.group(2).strip()
                        elif match.lastindex:
                            approach = match.group(0).strip()
                            why_failed = match.group(1).strip()
                        else:
                            approach = match.group(0).strip()
                            why_failed = ""

                        approach = self._clean_text(approach)
                        if len(approach) < 10 or self._is_duplicate(approach):
                            continue

                        dead_ends.append(
                            ExtractedDeadEnd(
                                approach=approach,
                                why_failed=self._clean_text(why_failed),
                                confidence=base_confidence,
                                source_turn=turn.turn_index,
                                timestamp=turn.timestamp,
                            )
                        )

            # Pattern 2: Tool chain dead ends (tool fails, then different tool used)
            failed_chains = [c for c in turn.tool_chains if not c.success]
            for chain in failed_chains:
                dead_end = self._extract_tool_chain_dead_end(chain, turn)
                if dead_end:
                    dead_ends.append(dead_end)

        return [d for d in dead_ends if d.confidence >= self.min_confidence]

    def extract_mistakes(self, turns: list[ConversationTurn]) -> list[ExtractedMistake]:
        """Extract mistakes from conversation turns."""
        mistakes = []

        for turn in turns:
            for text_source in [turn.assistant_text, turn.thinking, turn.user_message]:
                if not text_source:
                    continue

                for pattern, base_confidence in MISTAKE_PATTERNS:
                    for match in pattern.finditer(text_source):
                        mistake_text = match.group(1).strip() if match.lastindex else match.group(0).strip()
                        mistake_text = self._clean_text(mistake_text)

                        if len(mistake_text) < 10 or self._is_duplicate(mistake_text):
                            continue

                        # User corrections are higher confidence
                        confidence = base_confidence
                        if text_source == turn.user_message:
                            confidence = min(1.0, confidence + 0.10)

                        mistakes.append(
                            ExtractedMistake(
                                mistake=mistake_text,
                                confidence=confidence,
                                source_turn=turn.turn_index,
                                timestamp=turn.timestamp,
                            )
                        )

        return [m for m in mistakes if m.confidence >= self.min_confidence]

    def extract_unknowns(self, turns: list[ConversationTurn]) -> list[ExtractedUnknown]:
        """Extract unknowns/open questions from conversation turns."""
        unknowns = []

        for turn in turns:
            for text_source in [turn.assistant_text, turn.thinking]:
                if not text_source:
                    continue

                for pattern, base_confidence in UNKNOWN_PATTERNS:
                    # Skip the generic question pattern for assistant text
                    if base_confidence < 0.5 and text_source == turn.assistant_text:
                        continue

                    for match in pattern.finditer(text_source):
                        unknown_text = match.group(1).strip() if match.lastindex else match.group(0).strip()
                        unknown_text = self._clean_text(unknown_text)

                        if len(unknown_text) < 10 or self._is_duplicate(unknown_text):
                            continue

                        unknowns.append(
                            ExtractedUnknown(
                                unknown=unknown_text,
                                confidence=base_confidence,
                                source_turn=turn.turn_index,
                                timestamp=turn.timestamp,
                            )
                        )

        return [u for u in unknowns if u.confidence >= self.min_confidence]

    def extract_all(
        self,
        turns: list[ConversationTurn],
        source: str = "claude-code",
        session_id: str = "",
    ) -> ExtractionResult:
        """Run all extractors on conversation turns.

        Args:
            turns: List of conversation turns to process.
            source: Source identifier ("claude-code" or "claude-ai").
            session_id: Session ID for the extracted artifacts.

        Returns:
            ExtractionResult with all extracted artifacts.
        """
        result = ExtractionResult(
            findings=self.extract_findings(turns),
            decisions=self.extract_decisions(turns),
            dead_ends=self.extract_dead_ends(turns),
            mistakes=self.extract_mistakes(turns),
            unknowns=self.extract_unknowns(turns),
            source=source,
            session_id=session_id,
            turns_processed=len(turns),
            extraction_timestamp=datetime.now(timezone.utc).isoformat(),
        )

        return result

    def _extract_tool_chain_dead_end(self, chain, turn) -> ExtractedDeadEnd | None:
        """Extract a dead end from a failed tool chain invocation."""
        approach = f"Used {chain.tool_name}"
        if chain.tool_input:
            if "command" in chain.tool_input:
                approach += f": {chain.tool_input['command'][:100]}"
            elif "pattern" in chain.tool_input:
                approach += f": {chain.tool_input['pattern'][:100]}"

        if self._is_duplicate(approach):
            return None

        why_failed = ""
        if chain.result_content:
            for line in chain.result_content.split("\n"):
                if any(w in line.lower() for w in ["error", "fail", "not found", "denied"]):
                    why_failed = line.strip()[:200]
                    break

        return ExtractedDeadEnd(
            approach=approach,
            why_failed=why_failed,
            confidence=0.65,
            source_turn=turn.turn_index,
            timestamp=turn.timestamp,
        )

    # --- Helpers ---

    def _clean_text(self, text: str) -> str:
        """Clean extracted text for artifact storage."""
        # Remove markdown formatting
        text = re.sub(r"[*_`#]+", "", text)
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()
        # Remove trailing punctuation artifacts
        text = text.rstrip(".,;:")
        # Cap length
        if len(text) > 500:
            text = text[:497] + "..."
        return text

    def _estimate_impact(self, finding_text: str, turn: ConversationTurn) -> float:
        """Estimate the impact of a finding based on context."""
        impact = 0.5  # Default

        # Higher impact for findings about bugs, security, architecture
        high_impact_keywords = [
            "bug",
            "security",
            "vulnerability",
            "breaking",
            "regression",
            "architecture",
            "design",
            "performance",
            "critical",
        ]
        medium_impact_keywords = [
            "pattern",
            "convention",
            "approach",
            "structure",
            "dependency",
        ]

        text_lower = finding_text.lower()
        if any(kw in text_lower for kw in high_impact_keywords):
            impact = 0.8
        elif any(kw in text_lower for kw in medium_impact_keywords):
            impact = 0.6

        # Boost if finding came from deep investigation (many tool chains in turn)
        if len(turn.tool_chains) > 3:
            impact = min(1.0, impact + 0.1)

        return round(impact, 2)


# --- Deduplication Helpers ---


def build_dedup_set_from_db(db, project_id: str) -> set[str]:
    """Build a set of content hashes from existing artifacts in the database.

    Used to avoid importing duplicates of artifacts that already exist.
    """
    hashes = set()
    cursor = db.conn.cursor()

    # Findings
    try:
        cursor.execute("SELECT finding FROM project_findings WHERE project_id = ?", (project_id,))
        for row in cursor.fetchall():
            if row[0]:
                normalized = re.sub(r"\s+", " ", row[0].lower().strip())
                hashes.add(hashlib.sha256(normalized.encode()).hexdigest()[:16])
    except Exception:
        pass

    # Unknowns
    try:
        cursor.execute("SELECT unknown FROM project_unknowns WHERE project_id = ?", (project_id,))
        for row in cursor.fetchall():
            if row[0]:
                normalized = re.sub(r"\s+", " ", row[0].lower().strip())
                hashes.add(hashlib.sha256(normalized.encode()).hexdigest()[:16])
    except Exception:
        pass

    # Dead ends
    try:
        cursor.execute("SELECT approach FROM project_dead_ends WHERE project_id = ?", (project_id,))
        for row in cursor.fetchall():
            if row[0]:
                normalized = re.sub(r"\s+", " ", row[0].lower().strip())
                hashes.add(hashlib.sha256(normalized.encode()).hexdigest()[:16])
    except Exception:
        pass

    # Mistakes
    try:
        cursor.execute("SELECT mistake FROM mistakes_made WHERE project_id = ?", (project_id,))
        for row in cursor.fetchall():
            if row[0]:
                normalized = re.sub(r"\s+", " ", row[0].lower().strip())
                hashes.add(hashlib.sha256(normalized.encode()).hexdigest()[:16])
    except Exception:
        pass

    return hashes
