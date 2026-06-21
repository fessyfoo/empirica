"""Tests for ai_id genericization across the hook + statusline layer (1.12).

The hooks used to hardcode `ai_id='claude-code'` as the default identity in
six load-bearing sites plus two back-compat match-lists. On a multi-practice
machine (one box running empirica + cortex + autonomy + extension) that
default reads the wrong practice's sessions / calibration. The fix resolves
the canonical ai_id via `InstanceResolver.ai_id()` (project.yaml → basename),
falling back to the legacy 'claude-code' literal only when resolution yields
nothing.

Two behaviours are covered:

  • Load-bearing defaults: the resolved ai_id is what gets passed to
    `latest_session_id`; 'claude-code' is used iff `ai_id()` returns None.

  • Back-compat match-lists: the resolved id is *prepended* to the legacy
    ['claude-code', None] order, but ONLY when truthy. A leading None would
    short-circuit `latest_session_id` to the wildcard and skip the legacy
    lookup — the regression this guards against.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import empirica.utils.session_resolver as sr

_HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _load_hook(module_name: str, filename: str):
    """Import a hyphenated hook script as a module (hermetic re-import)."""
    sys.path.insert(0, str(_HOOKS))
    try:
        if module_name in sys.modules:
            del sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, _HOOKS / filename)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


@pytest.fixture
def patch_resolver(monkeypatch):
    """Patch InstanceResolver.ai_id + latest_session_id; capture call order.

    Returns a helper: configure(ai_id_value, lsid_behavior) → captured list.
    """
    captured: list = []

    def _configure(ai_id_value, *, raise_always=False, return_value="SESS"):
        monkeypatch.setattr(
            sr.InstanceResolver,
            "ai_id",
            staticmethod(lambda *_a, **_k: ai_id_value),
        )

        def _fake_lsid(*_a, **k):
            captured.append(k.get("ai_id"))
            if raise_always:
                raise ValueError("no session")
            return return_value

        monkeypatch.setattr(
            sr.InstanceResolver,
            "latest_session_id",
            staticmethod(_fake_lsid),
        )
        return captured

    return _configure


# ── Load-bearing defaults ───────────────────────────────────────────────


def test_session_end_postflight_uses_resolved_ai_id(patch_resolver):
    captured = patch_resolver("empirica")
    mod = _load_hook("session_end_postflight", "session-end-postflight.py")
    assert mod.get_active_session() == "SESS"
    assert captured == ["empirica"]  # resolved id, not 'claude-code'


def test_session_end_postflight_falls_back_when_unresolved(patch_resolver):
    captured = patch_resolver(None)
    mod = _load_hook("session_end_postflight", "session-end-postflight.py")
    mod.get_active_session()
    assert captured == ["claude-code"]  # legacy fallback when ai_id() is None


def test_subagent_start_uses_resolved_ai_id(patch_resolver):
    captured = patch_resolver("cortex")
    mod = _load_hook("subagent_start", "subagent-start.py")
    assert mod.get_parent_session_id() == "SESS"
    assert captured == ["cortex"]


def test_subagent_start_falls_back_when_unresolved(patch_resolver):
    captured = patch_resolver(None)
    mod = _load_hook("subagent_start", "subagent-start.py")
    mod.get_parent_session_id()
    assert captured == ["claude-code"]


# ── Back-compat match-lists (regression guard) ──────────────────────────


def test_pre_compact_prepends_resolved_then_legacy_order(patch_resolver):
    # raise_always → the loop walks every pattern; we assert the full order.
    captured = patch_resolver("empirica", raise_always=True)
    mod = _load_hook("pre_compact", "pre-compact.py")
    assert mod._detect_empirica_session() is None
    assert captured == ["empirica", "claude-code", None]


def test_pre_compact_no_none_prepend_when_unresolved(patch_resolver):
    # The regression: a leading None would short-circuit to the wildcard.
    captured = patch_resolver(None, raise_always=True)
    mod = _load_hook("pre_compact", "pre-compact.py")
    mod._detect_empirica_session()
    assert captured == ["claude-code", None]  # legacy order preserved, no leading None


def test_post_compact_prepends_resolved_then_legacy_order(patch_resolver):
    captured = patch_resolver("autonomy", raise_always=True)
    mod = _load_hook("post_compact", "post-compact.py")
    assert mod._get_empirica_session(claude_session_id=None) is None
    assert captured == ["autonomy", "claude-code", None]


def test_post_compact_no_none_prepend_when_unresolved(patch_resolver):
    captured = patch_resolver(None, raise_always=True)
    mod = _load_hook("post_compact", "post-compact.py")
    mod._get_empirica_session(claude_session_id=None)
    assert captured == ["claude-code", None]
