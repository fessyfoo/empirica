"""
Tests for ArtifactExtractor — epistemic artifact extraction from conversations.

Tests:
1. Finding extraction (explicit patterns, impact estimation)
2. Decision extraction
3. Dead-end extraction (text patterns + tool chain failures)
4. Mistake extraction (AI and user corrections)
5. Unknown extraction
6. Full extraction pipeline
7. Deduplication
8. Confidence filtering
"""

from empirica.core.canonical.artifact_extractor import (
    ArtifactExtractor,
    ExtractionResult,
)
from empirica.core.canonical.transcript_parser import ConversationTurn, ToolChain

# --- Helpers ---


def make_turn(
    user_message: str = "test question",
    assistant_text: str = "",
    thinking: str = "",
    tool_chains: list[ToolChain] | None = None,
    turn_index: int = 0,
    timestamp: str = "2026-03-24T10:00:00Z",
) -> ConversationTurn:
    return ConversationTurn(
        turn_index=turn_index,
        user_message=user_message,
        assistant_text=assistant_text,
        thinking=thinking,
        tool_chains=tool_chains or [],
        timestamp=timestamp,
    )


# --- Finding Extraction Tests ---


class TestFindingExtraction:
    def test_explicit_finding_pattern(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                assistant_text="I found that the middleware uses a chain-of-responsibility pattern for request handling."
            )
        ]

        findings = extractor.extract_findings(turns)
        assert len(findings) >= 1
        assert any("middleware" in f.finding.lower() for f in findings)

    def test_root_cause_finding(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                assistant_text="The root cause is a race condition in the session store that occurs under concurrent access."
            )
        ]

        findings = extractor.extract_findings(turns)
        assert len(findings) >= 1
        assert any("race condition" in f.finding.lower() for f in findings)

    def test_finding_in_thinking(self):
        extractor = ArtifactExtractor()
        turns = [make_turn(thinking="I discovered that the auth module imports from a deprecated package.")]

        findings = extractor.extract_findings(turns)
        assert len(findings) >= 1
        # Thinking findings get slight confidence boost
        assert any(f.confidence > 0.8 for f in findings)

    def test_impact_estimation_security(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(assistant_text="I found that there is a security vulnerability in the input validation layer.")
        ]

        findings = extractor.extract_findings(turns)
        assert len(findings) >= 1
        # Security findings should have high impact
        assert any(f.impact >= 0.7 for f in findings)

    def test_short_findings_filtered(self):
        extractor = ArtifactExtractor()
        turns = [make_turn(assistant_text="I found it works.")]

        findings = extractor.extract_findings(turns)
        # Too short to be a meaningful finding
        assert len(findings) == 0


class TestDecisionExtraction:
    def test_explicit_decision_pattern(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(assistant_text="I'll use SQLite instead of Postgres since this is a single-user application.")
        ]

        decisions = extractor.extract_decisions(turns)
        assert len(decisions) >= 1
        assert any("sqlite" in d.choice.lower() for d in decisions)

    def test_decision_with_instead_of(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                assistant_text="Instead of mocking the database, we should use a test database for integration tests."
            )
        ]

        decisions = extractor.extract_decisions(turns)
        assert len(decisions) >= 1

    def test_lets_decision(self):
        extractor = ArtifactExtractor()
        turns = [make_turn(assistant_text="Let's use the factory pattern for creating test fixtures across the suite.")]

        decisions = extractor.extract_decisions(turns)
        assert len(decisions) >= 1


class TestDeadEndExtraction:
    def test_explicit_dead_end(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                assistant_text="I tried using the passport.js library but it was too heavy for our JWT-only auth needs."
            )
        ]

        dead_ends = extractor.extract_dead_ends(turns)
        assert len(dead_ends) >= 1
        assert any("passport" in d.approach.lower() for d in dead_ends)

    def test_tool_chain_failure_dead_end(self):
        extractor = ArtifactExtractor()
        tool_chain = ToolChain(
            tool_name="Bash",
            tool_input={"command": "npm run build"},
            tool_use_id="t1",
            result_content="Error: Module not found: '@auth/core'",
            success=False,
        )
        turns = [make_turn(tool_chains=[tool_chain])]

        dead_ends = extractor.extract_dead_ends(turns)
        assert len(dead_ends) >= 1
        assert any("Bash" in d.approach for d in dead_ends)

    def test_didnt_work_pattern(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                assistant_text="That approach didn't work because the API has rate limiting that blocks batch requests."
            )
        ]

        dead_ends = extractor.extract_dead_ends(turns)
        assert len(dead_ends) >= 1


class TestMistakeExtraction:
    def test_explicit_mistake(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(assistant_text="My mistake — I should have checked the return type before using it as a string.")
        ]

        mistakes = extractor.extract_mistakes(turns)
        assert len(mistakes) >= 1

    def test_forgot_to_pattern(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                assistant_text="I forgot to handle the null case in the session lookup which caused the TypeError."
            )
        ]

        mistakes = extractor.extract_mistakes(turns)
        assert len(mistakes) >= 1

    def test_user_correction_higher_confidence(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                user_message="No, that's wrong — you should have used the async version of the API call.",
            )
        ]

        mistakes = extractor.extract_mistakes(turns)
        # User corrections are high confidence (they're the source of truth)
        if mistakes:
            assert any(m.confidence >= 0.7 for m in mistakes)


class TestUnknownExtraction:
    def test_explicit_unknown(self):
        extractor = ArtifactExtractor()
        turns = [make_turn(assistant_text="I'm not sure how the session store handles concurrent access under load.")]

        unknowns = extractor.extract_unknowns(turns)
        assert len(unknowns) >= 1
        assert any("concurrent" in u.unknown.lower() for u in unknowns)

    def test_need_to_investigate(self):
        extractor = ArtifactExtractor()
        turns = [make_turn(assistant_text="I need to investigate how the cache invalidation works across replicas.")]

        unknowns = extractor.extract_unknowns(turns)
        assert len(unknowns) >= 1


class TestFullExtraction:
    def test_extract_all(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                user_message="Fix the auth bug",
                assistant_text="I found that the token validation uses an outdated algorithm.",
                turn_index=0,
            ),
            make_turn(
                user_message="What about the session?",
                assistant_text="I tried using Redis for sessions but it didn't work because of the firewall rules. "
                "Let's use local file-based sessions instead.",
                turn_index=1,
            ),
            make_turn(
                user_message="That looks wrong",
                assistant_text="My mistake — I should have used the async client for the database connection.",
                thinking="I need to investigate whether the connection pool handles reconnects properly.",
                turn_index=2,
            ),
        ]

        result = extractor.extract_all(turns, source="claude-code", session_id="test-sess")

        assert isinstance(result, ExtractionResult)
        assert result.source == "claude-code"
        assert result.session_id == "test-sess"
        assert result.turns_processed == 3
        assert result.total_artifacts > 0

    def test_extraction_summary(self):
        extractor = ArtifactExtractor()
        turns = [
            make_turn(
                assistant_text="I found that the auth system uses JWT tokens. I decided to use RS256 for signing.",
            )
        ]

        result = extractor.extract_all(turns)
        summary = result.summary()

        assert "findings" in summary
        assert "decisions" in summary
        assert "total" in summary
        assert summary["total"] == result.total_artifacts


class TestDeduplication:
    def test_duplicate_content_filtered(self):
        extractor = ArtifactExtractor()

        # Same finding in two turns
        turns = [
            make_turn(
                assistant_text="I found that the middleware uses a chain-of-responsibility pattern for handling.",
                turn_index=0,
            ),
            make_turn(
                assistant_text="I found that the middleware uses a chain-of-responsibility pattern for handling.",
                turn_index=1,
            ),
        ]

        findings = extractor.extract_findings(turns)
        # Should deduplicate
        assert len(findings) == 1

    def test_existing_artifacts_deduped(self):
        # Simulate existing artifact hashes
        import hashlib

        existing_text = "the middleware uses a chain-of-responsibility pattern for handling"
        normalized = existing_text.lower().strip()
        existing_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]

        extractor = ArtifactExtractor(dedup_existing={existing_hash})
        turns = [
            make_turn(
                assistant_text="I found that the middleware uses a chain-of-responsibility pattern for handling.",
            )
        ]

        findings = extractor.extract_findings(turns)
        assert len(findings) == 0  # Already exists


class TestConfidenceFiltering:
    def test_filter_by_confidence(self):
        extractor = ArtifactExtractor(min_confidence=0.1)  # Low threshold to get everything

        turns = [
            make_turn(
                assistant_text="I found that the system uses microservices for scaling. "
                "I need to investigate the deployment pipeline.",
                turn_index=0,
            ),
        ]

        result = extractor.extract_all(turns)

        # Now filter to high confidence only
        filtered = result.filter_by_confidence(min_confidence=0.7)
        assert filtered.total_artifacts <= result.total_artifacts
