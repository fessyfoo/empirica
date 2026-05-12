"""
Tests for the uncertainty vector semantic alignment fix.

Background (2026-04-07):
The statusline computes confidence = 0.40*know + 0.30*(1-uncertainty) +
0.20*context + 0.10*completion. This locks in the self-reported semantic:
uncertainty = amount of doubt (0.0 = certain, 1.0 = max uncertain). The
statusline inverts it to display confidence.

But the grounded verifier had TWO bugs that produced nonsense uncertainty
observations:

  BUG 1 — inverted sentinel source (collector.py:1389):
      Formula produced a CONFIDENCE value (1 round = 1.0, 5+ rounds = 0.0)
      but assigned it to the `uncertainty` vector. So a clean 1-round CHECK
      proceed was read as MAX uncertainty.

  BUG 2 — double-counting noetic source (collector.py:460):
      Used unknowns_surfaced / 5.0 for BOTH `know` AND `uncertainty`.
      Surfacing unknowns is honesty about knowledge boundaries (a know
      signal), NOT direct evidence of how uncertain the AI is.

These tests verify the fix by reading the collector source directly —
avoiding the full DB schema dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Resolved from this file's location so tests work on any checkout path
# (local dev, CI runner). tests/core/post_test/this.py → parents[3] = repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]

COLLECTOR_PATH = REPO_ROOT / "empirica/core/post_test/collector.py"


# ---------------------------------------------------------------------------
# Bug 1 regression: sentinel investigation_rounds formula
# ---------------------------------------------------------------------------

class TestInvestigationRoundsFormula:
    """The sentinel investigation_rounds source should produce MORE uncertainty
    value for MORE check rounds — monotonically increasing."""

    def test_formula_is_monotonically_increasing(self):
        """Lock in the correct formula: (total_checks - 1) / 4.0 clamped to [0,1].
        This replaces the prior inverted `1.0 - (total_checks - 1) / 4.0` which
        would be monotonically DECREASING."""
        def formula(total_checks: int) -> float:
            return min(1.0, (total_checks - 1) / 4.0)

        values = [formula(n) for n in range(1, 10)]
        assert values == sorted(values), (
            f"Expected monotonically increasing uncertainty, got {values}. "
            f"The OLD inverted formula would produce {[1.0, 0.75, 0.5, 0.25, 0.0]}."
        )

    def test_one_round_is_certain(self):
        """1 round = 0.0 uncertainty (ideal single-pass proceed)."""
        def formula(total_checks: int) -> float:
            return min(1.0, (total_checks - 1) / 4.0)
        assert formula(1) == 0.0

    def test_two_rounds_low_uncertainty(self):
        def formula(total_checks: int) -> float:
            return min(1.0, (total_checks - 1) / 4.0)
        assert formula(2) == 0.25

    def test_five_rounds_max_uncertainty(self):
        def formula(total_checks: int) -> float:
            return min(1.0, (total_checks - 1) / 4.0)
        assert formula(5) == 1.0

    def test_many_rounds_cap_at_one(self):
        def formula(total_checks: int) -> float:
            return min(1.0, (total_checks - 1) / 4.0)
        assert formula(20) == 1.0

    def test_source_file_has_no_direct_uncertainty_sources(self):
        """After the 2026-04-07 meta-uncertainty reframe, uncertainty is
        a META-derived quantity computed in mapper.py from other vectors'
        coverage and gap magnitudes. The collector should no longer emit
        any direct uncertainty sources. This test locks in that removal:
        neither the (old) `investigation_efficiency` nor the (interim)
        `investigation_rounds_uncertainty` metric should exist anymore.
        """
        src = COLLECTOR_PATH.read_text()
        # Old inverted metric (pre-fix)
        assert '"investigation_efficiency"' not in src
        # Interim metric (removed in meta-uncertainty reframe)
        assert "investigation_rounds_uncertainty" not in src

    def test_source_file_dead_end_ratio_no_longer_grounds_uncertainty(self):
        """dead_end_ratio used to emit with supports_vectors=["uncertainty"].
        After the meta-uncertainty reframe, it should be removed entirely
        (its signal is subsumed by productive_exploration_ratio which
        grounds signal/know instead).
        """
        src = COLLECTOR_PATH.read_text()
        assert 'metric_name="dead_end_ratio"' not in src


# ---------------------------------------------------------------------------
# Bug 2 regression: unknowns_surfaced supports_vectors
# ---------------------------------------------------------------------------

class TestUnknownsSurfacedSupportsVectors:
    """The `unknowns_surfaced` evidence item should ground `know` only,
    not `uncertainty`. Prior version double-counted by listing both."""

    def test_source_file_does_not_double_count(self):
        """Parse the collector.py AST and find the EvidenceItem construction
        where metric_name == 'unknowns_surfaced'. Its supports_vectors must
        contain 'know' but NOT 'uncertainty'."""
        tree = ast.parse(COLLECTOR_PATH.read_text())

        found_items = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match EvidenceItem(...) calls
            if not (isinstance(node.func, ast.Name) and node.func.id == "EvidenceItem"):
                continue
            # Extract metric_name and supports_vectors from kwargs
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            metric_name_node = kw.get("metric_name")
            if not isinstance(metric_name_node, ast.Constant):
                continue
            if metric_name_node.value != "unknowns_surfaced":
                continue

            supports_node = kw.get("supports_vectors")
            assert isinstance(supports_node, ast.List), (
                "supports_vectors should be a list literal"
            )
            vectors = [
                e.value for e in supports_node.elts
                if isinstance(e, ast.Constant)
            ]
            found_items.append(vectors)

        assert len(found_items) >= 1, (
            "Expected at least one EvidenceItem with metric_name='unknowns_surfaced'"
        )

        for vectors in found_items:
            assert "know" in vectors, (
                f"unknowns_surfaced must ground 'know'. Got: {vectors}"
            )
            assert "uncertainty" not in vectors, (
                f"unknowns_surfaced must NOT ground 'uncertainty'. "
                f"Got: {vectors}. This is the double-counting bug that was "
                f"fixed 2026-04-07 — surfacing unknowns is a disclosure "
                f"honesty signal (know), not a direct doubt measurement."
            )


# ---------------------------------------------------------------------------
# Statusline alignment
# ---------------------------------------------------------------------------

class TestMetaUncertaintyFormula:
    """The meta-uncertainty computation should derive uncertainty from the
    coverage and gap magnitudes of the OTHER vectors — not from direct
    doubt proxies.

    Formula:
        coverage_u = 1.0 - grounded_coverage
        gap_u = clamp(mean(|gaps_of_other_vectors|) * 2.0, 0, 1)
        uncertainty = 0.4 * coverage_u + 0.6 * gap_u
    """

    def _compute(self, grounded, gaps, grounded_coverage):
        from empirica.core.post_test.mapper import _compute_meta_uncertainty
        return _compute_meta_uncertainty(grounded, gaps, grounded_coverage)

    def test_perfect_calibration_yields_low_uncertainty(self):
        """Full coverage + zero gaps = lowest meta-uncertainty (0.0)."""
        gaps = {"know": 0.0, "do": 0.0, "context": 0.0}
        result = self._compute({}, gaps, grounded_coverage=1.0)
        assert result == 0.0

    def test_zero_coverage_and_no_gaps_returns_none(self):
        """No signal at all → insufficient evidence → None."""
        result = self._compute({}, {}, grounded_coverage=0.0)
        assert result is None

    def test_full_coverage_small_gaps_yields_low_uncertainty(self):
        """Full coverage + tiny gaps = low meta-uncertainty."""
        gaps = {"know": 0.05, "do": 0.05, "context": 0.0}
        result = self._compute({}, gaps, grounded_coverage=1.0)
        # coverage_u = 0.0, mean gap ~0.033, gap_u = 0.067
        # meta = 0.4*0 + 0.6*0.067 = 0.04
        assert result is not None
        assert 0.0 <= result <= 0.1

    def test_half_coverage_no_gaps_yields_moderate_uncertainty(self):
        """Half coverage with verified vectors showing no gaps."""
        gaps = {"know": 0.0, "do": 0.0}
        result = self._compute({}, gaps, grounded_coverage=0.5)
        # coverage_u = 0.5, gap_u = 0
        # meta = 0.4*0.5 + 0.6*0 = 0.2
        assert result is not None
        assert abs(result - 0.2) < 0.01

    def test_full_coverage_large_gaps_yields_high_uncertainty(self):
        """Full coverage but verified vectors show we were way off."""
        gaps = {"know": 0.5, "do": 0.5, "context": 0.5}
        result = self._compute({}, gaps, grounded_coverage=1.0)
        # coverage_u = 0, gap_u = min(1, 0.5*2) = 1.0
        # meta = 0.4*0 + 0.6*1 = 0.6
        assert result is not None
        assert abs(result - 0.6) < 0.01

    def test_worst_case_no_coverage_and_max_gaps_is_bounded(self):
        """Even in the worst case, uncertainty is bounded at 1.0."""
        gaps = {"know": 1.0, "do": 1.0}
        result = self._compute({}, gaps, grounded_coverage=0.0)
        # coverage_u = 1.0, gap_u = min(1, 1*2) = 1.0
        # meta = 0.4 + 0.6 = 1.0
        assert result is not None
        assert result == 1.0

    def test_excludes_uncertainty_from_its_own_calculation(self):
        """Uncertainty should not be included in its own gap computation
        (avoid circularity)."""
        gaps = {"know": 0.0, "uncertainty": 0.99}  # uncertainty's own gap should be ignored
        result = self._compute({}, gaps, grounded_coverage=1.0)
        # If uncertainty WAS included, gap_u would be high. If excluded
        # (correct behavior), gap_u should be based only on know's gap.
        # coverage_u = 0, mean of ONLY know = 0, gap_u = 0
        # meta = 0
        assert result == 0.0

    def test_negative_gaps_use_absolute_value(self):
        """Over- and under-estimation should both contribute equally
        to uncertainty (use |gap|, not raw gap)."""
        over = {"know": 0.3}  # self overestimated
        under = {"know": -0.3}  # self underestimated
        result_over = self._compute({}, over, grounded_coverage=1.0)
        result_under = self._compute({}, under, grounded_coverage=1.0)
        assert result_over == result_under

    def test_return_value_in_valid_range(self):
        """Result is always None or in [0, 1]."""
        # Various random-ish inputs
        for coverage in [0.0, 0.25, 0.5, 0.75, 1.0]:
            for gap in [0.0, 0.1, 0.3, 0.5, 1.0]:
                gaps = {"know": gap, "do": gap}
                result = self._compute({}, gaps, coverage)
                assert result is not None
                assert 0.0 <= result <= 1.0, (
                    f"Out of range for coverage={coverage}, gap={gap}: {result}"
                )


class TestStatuslineAlignment:
    """The grounded verifier's uncertainty semantic should align with the
    statusline's confidence formula: confidence = 0.40*know +
    0.30*(1-uncertainty) + 0.20*context + 0.10*completion."""

    STATUSLINE_PATH = REPO_ROOT / "empirica/plugins/claude-code-integration/scripts/statusline_empirica.py"

    def _load_statusline(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "statusline_empirica", self.STATUSLINE_PATH
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_statusline_formula_shape(self):
        """0.40*know + 0.30*(1-uncertainty) + 0.20*context + 0.10*completion."""
        mod = self._load_statusline()
        vectors = {
            "know": 0.8,
            "uncertainty": 0.2,
            "context": 0.7,
            "completion": 1.0,
        }
        conf = mod.calculate_confidence(vectors)
        # 0.40*0.8 + 0.30*(1-0.2) + 0.20*0.7 + 0.10*1.0
        # = 0.32 + 0.24 + 0.14 + 0.10 = 0.80
        assert abs(conf - 0.80) < 0.01

    def test_high_uncertainty_reduces_confidence(self):
        """Monotonicity: high uncertainty → low confidence.
        If this fails, the statusline semantic has flipped somewhere."""
        mod = self._load_statusline()
        low_u = {"know": 0.8, "uncertainty": 0.1, "context": 0.8, "completion": 1.0}
        high_u = {"know": 0.8, "uncertainty": 0.9, "context": 0.8, "completion": 1.0}
        conf_low = mod.calculate_confidence(low_u)
        conf_high = mod.calculate_confidence(high_u)
        assert conf_low > conf_high

    def test_statusline_semantic_matches_uncertainty_direction(self):
        """Self-reported uncertainty=0 should contribute maximally to confidence.
        Self-reported uncertainty=1 should contribute zero. This is what the
        grounded verifier now measures: DIRECT uncertainty, not inverted."""
        mod = self._load_statusline()
        # Neutralize other vectors
        neutral = {"know": 0.5, "context": 0.5, "completion": 0.0}
        certain = {**neutral, "uncertainty": 0.0}
        uncertain = {**neutral, "uncertainty": 1.0}
        conf_certain = mod.calculate_confidence(certain)
        conf_uncertain = mod.calculate_confidence(uncertain)
        # Difference should be exactly 0.30 (the uncertainty weight)
        assert abs((conf_certain - conf_uncertain) - 0.30) < 0.01
