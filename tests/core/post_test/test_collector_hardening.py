"""Tests for the collector's insufficient-evidence and source-error handling.

These cover the 'fail loudly' layer added by the sentinel measurer remote-ops
design (2026-04-08):
  - EvidenceBundle has sources_empty (ran ok, returned 0 items) and
    source_errors (collector raised; type+message captured)
  - EvidenceProfile has INSUFFICIENT enum value
  - _resolve_profile() returns INSUFFICIENT for empty changed files
  - collect_all() skips profile collectors when profile is INSUFFICIENT
  - collect_all() distinguishes empty from failed sources and captures error info
"""

from __future__ import annotations

from empirica.core.post_test.collector import (
    EvidenceBundle,
    EvidenceProfile,
    PostTestCollector,
)

# ---------------------------------------------------------------------------
# EvidenceBundle: new fields (Task 3)
# ---------------------------------------------------------------------------


def test_evidence_bundle_has_sources_empty_field():
    """EvidenceBundle should expose a sources_empty list (default empty)."""
    bundle = EvidenceBundle(session_id="test")
    assert hasattr(bundle, "sources_empty")
    assert bundle.sources_empty == []


def test_evidence_bundle_has_source_errors_field():
    """EvidenceBundle should expose a source_errors dict (default empty)."""
    bundle = EvidenceBundle(session_id="test")
    assert hasattr(bundle, "source_errors")
    assert bundle.source_errors == {}


def test_evidence_bundle_source_errors_accepts_dict_entries():
    """source_errors should accept string key/value entries (type: msg)."""
    bundle = EvidenceBundle(session_id="test")
    bundle.source_errors["foo"] = "RuntimeError: simulated"
    assert bundle.source_errors == {"foo": "RuntimeError: simulated"}


# ---------------------------------------------------------------------------
# EvidenceProfile.INSUFFICIENT (Task 6)
# ---------------------------------------------------------------------------


def test_evidence_profile_has_insufficient_value():
    """EvidenceProfile should expose INSUFFICIENT alongside CODE/PROSE/etc."""
    assert hasattr(EvidenceProfile, "INSUFFICIENT")
    assert EvidenceProfile.INSUFFICIENT == "insufficient"
    assert EvidenceProfile.INSUFFICIENT in EvidenceProfile.VALID


# ---------------------------------------------------------------------------
# _resolve_profile() returns INSUFFICIENT for empty changed files (Task 7)
# ---------------------------------------------------------------------------


def _force_auto_profile(collector, monkeypatch):
    """Force EvidenceProfile.resolve() into the AUTO branch by stubbing
    _resolve_project_root to None — otherwise the project.yaml's
    `evidence_profile: code` short-circuits the resolution before AUTO.
    """
    monkeypatch.setattr(collector, "_resolve_project_root", lambda: None)
    monkeypatch.delenv("EMPIRICA_EVIDENCE_PROFILE", raising=False)


def test_resolve_profile_returns_insufficient_when_no_changed_files(monkeypatch):
    """When AUTO and no changed files, profile should be INSUFFICIENT not PROSE.

    This is the structural backstop for out-of-repo work and remote-ops without
    explicit declaration — instead of silently falling back to prose grading,
    the measurer correctly reports insufficient grounding.
    """
    collector = PostTestCollector(session_id="test", phase="praxic")
    _force_auto_profile(collector, monkeypatch)
    monkeypatch.setattr(collector, "_get_session_changed_files", lambda: [])

    profile = collector._resolve_profile()
    assert profile == EvidenceProfile.INSUFFICIENT


def test_resolve_profile_still_returns_prose_when_only_markdown_changed(monkeypatch):
    """Markdown/text files (not .py, not web) should still produce PROSE.

    Regression check — the INSUFFICIENT fallback only kicks in for empty
    changed-files, not for any change that isn't code or web.
    """
    collector = PostTestCollector(session_id="test", phase="praxic")
    _force_auto_profile(collector, monkeypatch)
    monkeypatch.setattr(
        collector,
        "_get_session_changed_files",
        lambda: ["README.md", "notes.txt"],
    )
    profile = collector._resolve_profile()
    assert profile == EvidenceProfile.PROSE


def test_resolve_profile_returns_code_for_python_files(monkeypatch):
    """Python files trigger CODE profile (existing behavior, regression check)."""
    collector = PostTestCollector(session_id="test", phase="praxic")
    _force_auto_profile(collector, monkeypatch)
    monkeypatch.setattr(
        collector,
        "_get_session_changed_files",
        lambda: ["empirica/foo.py"],
    )
    profile = collector._resolve_profile()
    assert profile == EvidenceProfile.CODE


# ---------------------------------------------------------------------------
# collect_all() skips profile collectors when profile is INSUFFICIENT (Task 8)
# ---------------------------------------------------------------------------


def test_insufficient_profile_skips_profile_collectors(monkeypatch):
    """When profile=INSUFFICIENT, no profile-specific collectors run.

    Universal collectors still run (they grade session state, not file changes).
    Profile-specific collectors (pytest, git, code_quality, prose, web) DO NOT
    run, because there's no signal for them to grade.
    """
    collector = PostTestCollector(session_id="test", phase="praxic")
    monkeypatch.setattr(collector, "_resolve_profile", lambda: EvidenceProfile.INSUFFICIENT)
    monkeypatch.setattr(collector, "_get_db", lambda: None)

    invoked: list[str] = []

    def make_stub(name: str):
        def _stub(*args, **kwargs):
            invoked.append(name)
            return []

        return _stub

    universal_methods = [
        "_collect_artifact_metrics",
        "_collect_goal_metrics",
        "_collect_issue_metrics",
        "_collect_triage_metrics",
        "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
    ]
    profile_methods = [
        "_collect_test_results",
        "_collect_git_metrics",
        "_collect_code_quality_metrics",
    ]
    for m in universal_methods + profile_methods:
        monkeypatch.setattr(collector, m, make_stub(m))

    collector.collect_all()

    # Universal collectors run
    assert "_collect_artifact_metrics" in invoked
    assert "_collect_goal_metrics" in invoked

    # Profile-specific collectors do NOT run
    assert "_collect_test_results" not in invoked, "pytest collector should not run for INSUFFICIENT profile"
    assert "_collect_git_metrics" not in invoked, "git collector should not run for INSUFFICIENT profile"
    assert "_collect_code_quality_metrics" not in invoked, (
        "code_quality collector should not run for INSUFFICIENT profile"
    )


# ---------------------------------------------------------------------------
# collect_all() error capture and empty/failed distinction (Task 9)
# ---------------------------------------------------------------------------


def test_collect_all_captures_source_errors_with_type_and_message(monkeypatch):
    """When a collector raises, source_errors should capture the exception
    type and message keyed by source name."""
    collector = PostTestCollector(session_id="test", phase="praxic")
    monkeypatch.setattr(collector, "_resolve_profile", lambda: EvidenceProfile.INSUFFICIENT)
    monkeypatch.setattr(collector, "_get_db", lambda: None)

    def boom():
        raise RuntimeError("simulated source failure")

    monkeypatch.setattr(collector, "_collect_artifact_metrics", boom)
    # Stub the other universal collectors as no-ops
    for method in [
        "_collect_goal_metrics",
        "_collect_issue_metrics",
        "_collect_triage_metrics",
        "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
    ]:
        monkeypatch.setattr(collector, method, lambda: [])

    bundle = collector.collect_all()

    assert "artifacts" in bundle.sources_failed
    assert "artifacts" in bundle.source_errors
    err = bundle.source_errors["artifacts"]
    assert "RuntimeError" in err
    assert "simulated source failure" in err
    # Failed source should NOT be in sources_available or sources_empty
    assert "artifacts" not in bundle.sources_available
    assert "artifacts" not in bundle.sources_empty


def test_collect_all_distinguishes_empty_from_failed(monkeypatch):
    """A collector returning [] goes to sources_empty; a collector raising
    goes to sources_failed. They are mutually exclusive."""
    collector = PostTestCollector(session_id="test", phase="praxic")
    monkeypatch.setattr(collector, "_resolve_profile", lambda: EvidenceProfile.INSUFFICIENT)
    monkeypatch.setattr(collector, "_get_db", lambda: None)

    # artifacts: returns empty
    monkeypatch.setattr(collector, "_collect_artifact_metrics", lambda: [])
    # other universal collectors as no-ops
    for method in [
        "_collect_goal_metrics",
        "_collect_issue_metrics",
        "_collect_triage_metrics",
        "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
    ]:
        monkeypatch.setattr(collector, method, lambda: [])

    bundle = collector.collect_all()

    # Returned [] → sources_empty
    assert "artifacts" in bundle.sources_empty
    # NOT in sources_failed
    assert "artifacts" not in bundle.sources_failed
    # NOT in sources_available (didn't contribute items)
    assert "artifacts" not in bundle.sources_available


def test_collect_all_source_errors_truncates_long_messages(monkeypatch):
    """Long exception messages should be truncated in source_errors so
    POSTFLIGHT JSON output doesn't blow up on a 10KB stack trace string."""
    collector = PostTestCollector(session_id="test", phase="praxic")
    monkeypatch.setattr(collector, "_resolve_profile", lambda: EvidenceProfile.INSUFFICIENT)
    monkeypatch.setattr(collector, "_get_db", lambda: None)

    long_message = "x" * 1000  # 1000-char message

    def boom():
        raise ValueError(long_message)

    monkeypatch.setattr(collector, "_collect_artifact_metrics", boom)
    for method in [
        "_collect_goal_metrics",
        "_collect_issue_metrics",
        "_collect_triage_metrics",
        "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
    ]:
        monkeypatch.setattr(collector, method, lambda: [])

    bundle = collector.collect_all()

    err = bundle.source_errors["artifacts"]
    # Should be truncated to a reasonable length (200 chars + type prefix)
    assert len(err) < 300, f"source_errors entry too long: {len(err)} chars"
    assert err.startswith("ValueError:")
