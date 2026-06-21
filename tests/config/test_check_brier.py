"""
Tests for B4 Check-outcome Brier scoring (SPEC 1 Wave 3).

Verifies: Brier computation from check predictions vs actuals,
per-check breakdown, no-prediction pass-through.
"""

from __future__ import annotations

from empirica.core.post_test.dynamic_thresholds import compute_check_brier


class TestComputeCheckBrier:
    def test_no_predictions_returns_none(self):
        """When no checks have predictions, returns None."""
        results = [
            {"check_id": "tests", "passed": True},
            {"check_id": "lint", "passed": True},
        ]
        assert compute_check_brier(results) is None

    def test_perfect_predictions(self):
        """Predicted 1.0 for passing checks = Brier 0."""
        results = [
            {"check_id": "tests", "passed": True, "predicted_pass": 1.0},
            {"check_id": "lint", "passed": True, "predicted_pass": 1.0},
        ]
        brier = compute_check_brier(results)
        assert brier is not None
        assert brier["brier_score"] == 0.0
        assert brier["n_predictions"] == 2
        assert brier["interpretation"] == "perfect"

    def test_worst_predictions(self):
        """Predicted 1.0 for failing checks = Brier 1.0."""
        results = [
            {"check_id": "tests", "passed": False, "predicted_pass": 1.0},
            {"check_id": "lint", "passed": False, "predicted_pass": 1.0},
        ]
        brier = compute_check_brier(results)
        assert brier is not None
        assert brier["brier_score"] == 1.0
        assert brier["interpretation"] == "poor"

    def test_mixed_predictions(self):
        """Some right, some wrong — score between 0 and 1."""
        results = [
            {"check_id": "tests", "passed": True, "predicted_pass": 0.9},
            {"check_id": "lint", "passed": False, "predicted_pass": 0.8},
        ]
        brier = compute_check_brier(results)
        assert brier is not None
        # (0.9-1)^2 = 0.01, (0.8-0)^2 = 0.64, mean = 0.325
        assert abs(brier["brier_score"] - 0.325) < 0.01
        assert brier["n_predictions"] == 2

    def test_per_check_breakdown(self):
        results = [
            {"check_id": "tests", "passed": True, "predicted_pass": 0.9},
            {"check_id": "lint", "passed": True, "predicted_pass": 0.5},
        ]
        brier = compute_check_brier(results)
        assert brier is not None
        assert len(brier["per_check"]) == 2
        tests_check = next(c for c in brier["per_check"] if c["check_id"] == "tests")
        assert tests_check["predicted_pass"] == 0.9
        assert tests_check["actual_pass"] is True
        assert tests_check["brier_contribution"] == 0.01

    def test_only_predicted_checks_scored(self):
        """Checks without predictions are excluded from Brier."""
        results = [
            {"check_id": "tests", "passed": True, "predicted_pass": 1.0},
            {"check_id": "lint", "passed": True},  # no prediction
            {"check_id": "semgrep", "passed": False, "predicted_pass": 0.0},
        ]
        brier = compute_check_brier(results)
        assert brier is not None
        assert brier["n_predictions"] == 2  # only tests + semgrep
        assert brier["brier_score"] == 0.0  # both perfect
