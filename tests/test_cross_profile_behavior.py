#!/usr/bin/env python3
"""
Cross-Profile Behavior Validation Test

Proves that different investigation profiles produce observably different behaviors
in threshold evaluation, tuning weights, and investigation constraints.

This test validates the core profile system for the November 20, 2025 launch.
"""

import pytest

from empirica.config.profile_loader import ProfileLoader, load_profile


class TestCrossProfileBehavior:
    """Test that profiles have distinct, measurable behavior differences"""

    @pytest.fixture
    def loader(self):
        """Load all investigation profiles"""
        return ProfileLoader()

    @pytest.fixture
    def all_profiles(self):
        """Get all 5 profiles"""
        profile_names = [
            "high_reasoning_collaborative",
            "autonomous_agent",
            "critical_domain",
            "exploratory",
            "balanced",
        ]
        return {name: load_profile(name) for name in profile_names}

    # =========================================================================
    # TEST 1: Max Investigation Rounds Differ
    # =========================================================================

    def test_max_rounds_differ_across_profiles(self, all_profiles):
        """Verify max_rounds varies significantly between profiles"""

        max_rounds = {name: profile.investigation.max_rounds for name, profile in all_profiles.items()}

        # Expected values
        assert max_rounds["high_reasoning_collaborative"] is None, "High reasoning should have unlimited rounds"
        assert max_rounds["autonomous_agent"] == 5, "Autonomous agent should have limited rounds (5)"
        assert max_rounds["critical_domain"] == 3, "Critical domain should have very limited rounds (3)"
        assert max_rounds["exploratory"] is None, "Exploratory should have unlimited rounds"
        assert max_rounds["balanced"] == 7, "Balanced should have moderate rounds (7)"

        # Verify at least 3 different values exist
        unique_values = {v for v in max_rounds.values() if v is not None}
        assert len(unique_values) >= 3, f"Expected at least 3 different max_rounds values, got {unique_values}"

    # =========================================================================
    # TEST 2: Confidence Thresholds Differ
    # =========================================================================

    def test_confidence_thresholds_differ(self, all_profiles):
        """Verify confidence thresholds vary between profiles"""

        # Get thresholds (handling dynamic/adaptive thresholds)
        thresholds = {}
        for name, profile in all_profiles.items():
            if hasattr(profile.investigation, "confidence_threshold"):
                thresh = profile.investigation.confidence_threshold
                # Handle string thresholds (dynamic/adaptive)
                if isinstance(thresh, str):
                    thresh = None  # Dynamic means no fixed threshold
                thresholds[name] = thresh

        # Expected behavior
        assert thresholds["autonomous_agent"] == 0.70, "Autonomous agent should have clear threshold (0.70)"
        assert thresholds["critical_domain"] == 0.90, "Critical domain should have high threshold (0.90)"
        assert thresholds["balanced"] == 0.65, "Balanced should have moderate threshold (0.65)"

        # Verify critical_domain has highest threshold
        numeric_thresholds = {k: v for k, v in thresholds.items() if v is not None}
        assert thresholds["critical_domain"] == max(numeric_thresholds.values()), (
            "Critical domain should have highest confidence threshold"
        )

    # =========================================================================
    # TEST 3: Tuning Weights Differ
    # =========================================================================

    def test_tuning_weights_differ(self, all_profiles):
        """Verify tuning weights vary significantly between profiles"""

        # Extract foundation weights (most variable)
        foundation_weights = {name: profile.tuning.foundation_weight for name, profile in all_profiles.items()}

        # Expected differentiation
        assert foundation_weights["high_reasoning_collaborative"] == 1.0, (
            "High reasoning should have normal foundation weight (1.0)"
        )
        assert foundation_weights["autonomous_agent"] == 1.2, "Autonomous should emphasize foundation (1.2)"
        assert foundation_weights["critical_domain"] == 1.5, "Critical domain should heavily emphasize foundation (1.5)"
        assert foundation_weights["exploratory"] == 0.7, "Exploratory should de-emphasize foundation (0.7)"

        # Verify range is significant (at least 0.5 difference)
        weight_range = max(foundation_weights.values()) - min(foundation_weights.values())
        assert weight_range >= 0.5, f"Foundation weight range should be >= 0.5, got {weight_range}"

    # =========================================================================
    # TEST 4: Tool Suggestion Modes Differ
    # =========================================================================

    def test_tool_suggestion_modes_differ(self, all_profiles):
        """Verify tool suggestion modes vary between profiles"""

        tool_modes = {name: profile.investigation.tool_suggestion_mode.value for name, profile in all_profiles.items()}

        # Expected modes
        assert tool_modes["high_reasoning_collaborative"] == "light", (
            "High reasoning should have light tool suggestions"
        )
        assert tool_modes["autonomous_agent"] == "guided", "Autonomous should have guided tool suggestions"
        assert tool_modes["critical_domain"] == "prescribed", "Critical domain should have prescribed tools"
        assert tool_modes["exploratory"] == "inspirational", "Exploratory should have inspirational suggestions"
        assert tool_modes["balanced"] == "suggestive", "Balanced should have suggestive suggestions"

        # Verify all modes are different
        assert len(set(tool_modes.values())) == 5, f"Expected 5 unique tool modes, got {len(set(tool_modes.values()))}"

    # =========================================================================
    # TEST 5: Override Permissions Differ
    # =========================================================================

    def test_override_permissions_differ(self, all_profiles):
        """Verify threshold override permissions vary between profiles"""

        override_allowed = {name: profile.action_thresholds.override_allowed for name, profile in all_profiles.items()}

        # Expected permissions
        assert override_allowed["high_reasoning_collaborative"] is True, (
            "High reasoning should allow threshold overrides"
        )
        assert override_allowed["autonomous_agent"] is False, "Autonomous should not allow overrides"
        assert override_allowed["critical_domain"] is False, "Critical domain should not allow overrides"
        assert override_allowed["exploratory"] is True, "Exploratory should allow overrides"
        assert override_allowed["balanced"] is True, "Balanced should allow overrides"

        # Verify mix of True/False
        assert True in override_allowed.values() and False in override_allowed.values(), (
            "Expected mix of allowed/disallowed override permissions"
        )

    # =========================================================================
    # TEST 6: Action Threshold Combinations Differ
    # =========================================================================

    def test_action_threshold_combinations_differ(self, all_profiles):
        """Verify action thresholds create distinct behavior profiles"""

        # Get threshold fingerprints
        fingerprints = {}
        for name, profile in all_profiles.items():
            fingerprints[name] = (
                profile.action_thresholds.uncertainty_high,
                profile.action_thresholds.clarity_low,
                profile.action_thresholds.foundation_low,
                profile.action_thresholds.confidence_proceed_min,
            )

        # Verify all fingerprints are unique
        assert len(set(fingerprints.values())) == 5, "Expected 5 unique threshold combinations (fingerprints)"

        # Verify critical_domain is most conservative
        critical_fingerprint = fingerprints["critical_domain"]
        assert critical_fingerprint[3] == 0.90, "Critical domain should have highest proceed threshold (0.90)"

        # Verify exploratory is most permissive
        exploratory_fingerprint = fingerprints["exploratory"]
        assert exploratory_fingerprint[3] == 0.50, "Exploratory should have lowest proceed threshold (0.50)"

    # =========================================================================
    # TEST 7: Postflight Modes Differ
    # =========================================================================

    def test_postflight_modes_differ(self, all_profiles):
        """Verify postflight assessment modes vary between profiles"""

        postflight_modes = {name: profile.learning.postflight_mode.value for name, profile in all_profiles.items()}

        # Expected modes
        assert postflight_modes["high_reasoning_collaborative"] == "genuine_reassessment", (
            "High reasoning should use genuine reassessment"
        )
        assert postflight_modes["autonomous_agent"] == "comparative_assessment", (
            "Autonomous should use comparative assessment"
        )
        assert postflight_modes["critical_domain"] == "full_audit_trail", "Critical domain should use full audit trail"
        assert postflight_modes["exploratory"] == "reflection", "Exploratory should use reflection mode"

        # Verify multiple different modes exist
        unique_modes = set(postflight_modes.values())
        assert len(unique_modes) >= 4, f"Expected at least 4 unique postflight modes, got {len(unique_modes)}"

    # =========================================================================
    # TEST 8: Uncertainty Weight Sensitivity Differs
    # =========================================================================

    def test_uncertainty_weight_sensitivity_differs(self, all_profiles):
        """Verify uncertainty sensitivity varies significantly"""

        uncertainty_weights = {name: profile.tuning.uncertainty_weight for name, profile in all_profiles.items()}

        # Critical domain should be most sensitive to uncertainty
        assert uncertainty_weights["critical_domain"] == 1.4, (
            "Critical domain should be highly sensitive to uncertainty (1.4)"
        )

        # Exploratory should be least sensitive
        assert uncertainty_weights["exploratory"] == 0.7, "Exploratory should be least sensitive to uncertainty (0.7)"

        # Verify significant range
        weight_range = max(uncertainty_weights.values()) - min(uncertainty_weights.values())
        assert weight_range >= 0.5, f"Uncertainty weight range should be >= 0.5, got {weight_range}"

    # =========================================================================
    # TEST 9: Novel Approaches Permission Differs
    # =========================================================================

    def test_novel_approaches_permission_differs(self, all_profiles):
        """Verify permission for novel approaches varies"""

        allow_novel = {name: profile.investigation.allow_novel_approaches for name, profile in all_profiles.items()}

        # Expected permissions
        assert allow_novel["high_reasoning_collaborative"] is True, "High reasoning should allow novel approaches"
        assert allow_novel["autonomous_agent"] is False, "Autonomous should not allow novel approaches"
        assert allow_novel["critical_domain"] is False, "Critical domain should not allow novel approaches"
        assert allow_novel["exploratory"] is True, "Exploratory should allow novel approaches"

        # Verify mix
        assert True in allow_novel.values() and False in allow_novel.values(), (
            "Expected mix of allowed/disallowed novel approaches"
        )

    # =========================================================================
    # TEST 10: Profile Behavior Fingerprint Uniqueness
    # =========================================================================

    def test_profile_behavior_fingerprints_are_unique(self, all_profiles):
        """Verify each profile has a unique behavioral fingerprint"""

        # Create comprehensive behavioral fingerprints
        fingerprints = {}
        for name, profile in all_profiles.items():
            fingerprints[name] = {
                "max_rounds": profile.investigation.max_rounds,
                "tool_mode": profile.investigation.tool_suggestion_mode.value,
                "override_allowed": profile.action_thresholds.override_allowed,
                "foundation_weight": profile.tuning.foundation_weight,
                "uncertainty_weight": profile.tuning.uncertainty_weight,
                "postflight_mode": profile.learning.postflight_mode.value,
                "allow_novel": profile.investigation.allow_novel_approaches,
            }

        # Convert to comparable tuples
        fingerprint_tuples = {name: tuple(sorted(fp.items())) for name, fp in fingerprints.items()}

        # Verify all fingerprints are unique
        assert len(set(fingerprint_tuples.values())) == 5, (
            "Expected 5 unique behavioral fingerprints across all profiles"
        )

        # Verify each profile differs in at least 2 dimensions
        from itertools import combinations

        for (name1, fp1), (name2, fp2) in combinations(fingerprints.items(), 2):
            differences = sum(1 for key in fp1 if fp1[key] != fp2[key])
            assert differences >= 2, (
                f"Profiles {name1} and {name2} should differ in at least 2 dimensions, got {differences}"
            )

    # =========================================================================
    # TEST 11: Profile Loading Performance
    # =========================================================================

    def test_profile_loading_is_fast(self):
        """Verify profile loading is fast enough for production"""
        import time

        start = time.time()
        for _ in range(100):
            load_profile("balanced")
        duration = time.time() - start

        # Should load 100 profiles in under 1 second
        assert duration < 1.0, f"Loading 100 profiles took {duration:.3f}s, should be < 1.0s"

    # =========================================================================
    # TEST 12: Profile Serialization Roundtrip
    # =========================================================================

    def test_profile_serialization_roundtrip(self, all_profiles):
        """Verify profiles can be serialized and deserialized without loss"""

        for name, profile in all_profiles.items():
            # Serialize to dict
            profile_dict = profile.to_dict()

            # Verify key fields present
            assert "name" in profile_dict
            assert "investigation" in profile_dict
            assert "action_thresholds" in profile_dict
            assert "tuning" in profile_dict

            # Verify values match
            assert profile_dict["name"] == name
            assert profile_dict["investigation"]["max_rounds"] == profile.investigation.max_rounds
            assert profile_dict["tuning"]["foundation_weight"] == profile.tuning.foundation_weight


# =============================================================================
# INTEGRATION TESTS: Profile Impact on CASCADE Behavior
# =============================================================================


class TestProfileCASCADEIntegration:
    """Test that profiles actually affect CASCADE workflow decisions"""

    @pytest.fixture
    def mock_assessment(self):
        """Mock epistemic assessment for testing"""
        return {
            "know": 0.50,
            "do": 0.50,
            "context": 0.50,
            "clarity": 0.50,
            "coherence": 0.60,
            "signal": 0.50,
            "density": 0.50,
            "state": 0.50,
            "change": 0.60,
            "completion": 0.50,
            "impact": 0.50,
            "engagement": 0.70,
            "uncertainty": 0.60,
        }

    def test_critical_domain_blocks_low_confidence_proceed(self, mock_assessment):
        """Verify critical_domain profile blocks proceeding at low confidence"""
        critical_profile = load_profile("critical_domain")

        # Calculate overall confidence (simplified)
        overall_confidence = (
            (mock_assessment["know"] + mock_assessment["do"] + mock_assessment["context"]) / 3 * 0.35
            + (mock_assessment["clarity"] + mock_assessment["coherence"]) / 2 * 0.25
            + (
                mock_assessment["state"]
                + mock_assessment["change"]
                + mock_assessment["completion"]
                + mock_assessment["impact"]
            )
            / 4
            * 0.25
            + mock_assessment["engagement"] * 0.15
        )

        # With critical_domain profile threshold of 0.90
        assert overall_confidence < critical_profile.action_thresholds.confidence_proceed_min, (
            "Mock assessment should be below critical_domain proceed threshold"
        )

    def test_exploratory_allows_low_confidence_proceed(self, mock_assessment):
        """Verify exploratory profile allows proceeding at low confidence"""
        exploratory_profile = load_profile("exploratory")

        # Calculate overall confidence (same as above)
        overall_confidence = (
            (mock_assessment["know"] + mock_assessment["do"] + mock_assessment["context"]) / 3 * 0.35
            + (mock_assessment["clarity"] + mock_assessment["coherence"]) / 2 * 0.25
            + (
                mock_assessment["state"]
                + mock_assessment["change"]
                + mock_assessment["completion"]
                + mock_assessment["impact"]
            )
            / 4
            * 0.25
            + mock_assessment["engagement"] * 0.15
        )

        # With exploratory profile threshold of 0.50
        assert overall_confidence >= exploratory_profile.action_thresholds.confidence_proceed_min, (
            "Mock assessment should be above exploratory proceed threshold"
        )


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
