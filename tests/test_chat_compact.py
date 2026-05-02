"""Tests for Phase 10 — chat compact lifecycle hooks."""

from __future__ import annotations

from empirica.core.chat.compact import (
    Breadcrumb,
    _format_yaml,
    _parse_yaml,
    format_recovery_message,
    load_breadcrumb,
    save_breadcrumb,
)


class TestSaveLoadRoundtrip:
    def test_save_creates_file(self, tmp_path):
        path = save_breadcrumb(
            session_id="abc-123",
            provider_name="ollama",
            model="qwen3.5",
            autonomy_mode="copilot",
            statusline_mode="full",
            recent_turns=[{"kind": "user", "text": "hi"}],
            root=tmp_path,
        )
        assert path.exists()
        assert path.name == "abc-123.yaml"

    def test_load_returns_breadcrumb(self, tmp_path):
        save_breadcrumb(
            session_id="abc-123",
            provider_name="ollama",
            model="qwen3.5",
            autonomy_mode="copilot",
            statusline_mode="full",
            recent_turns=[{"kind": "user", "text": "hi"}, {"kind": "agent_text", "text": "hello"}],
            root=tmp_path,
        )
        bc = load_breadcrumb("abc-123", root=tmp_path)
        assert bc is not None
        assert bc.session_id == "abc-123"
        assert bc.provider_name == "ollama"
        assert bc.model == "qwen3.5"
        assert bc.autonomy_mode == "copilot"
        assert bc.statusline_mode == "full"
        assert len(bc.recent_turns) == 2
        assert bc.recent_turns[0]["kind"] == "user"
        assert bc.recent_turns[1]["text"] == "hello"

    def test_load_returns_none_for_missing_session(self, tmp_path):
        assert load_breadcrumb("nope", root=tmp_path) is None

    def test_load_tolerates_corrupt_yaml(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("not: real: yaml: chaos\n[")
        bc = load_breadcrumb("broken", root=tmp_path)
        # Either returns a partial Breadcrumb (defaults applied) or None
        # — both are acceptable; what matters is it doesn't crash.
        if bc is not None:
            assert isinstance(bc, Breadcrumb)

    def test_save_overwrites_existing(self, tmp_path):
        save_breadcrumb(
            session_id="abc-123",
            provider_name="ollama",
            model="m1",
            autonomy_mode="assistant",
            statusline_mode="default",
            root=tmp_path,
        )
        save_breadcrumb(
            session_id="abc-123",
            provider_name="deepseek",
            model="m2",
            autonomy_mode="autonomous",
            statusline_mode="learning",
            root=tmp_path,
        )
        bc = load_breadcrumb("abc-123", root=tmp_path)
        assert bc is not None
        assert bc.provider_name == "deepseek"
        assert bc.autonomy_mode == "autonomous"

    def test_save_with_none_provider_and_model(self, tmp_path):
        save_breadcrumb(
            session_id="abc-123",
            provider_name=None,
            model=None,
            autonomy_mode="assistant",
            statusline_mode="default",
            root=tmp_path,
        )
        bc = load_breadcrumb("abc-123", root=tmp_path)
        assert bc is not None
        assert bc.provider_name is None
        assert bc.model is None


class TestFormatRecoveryMessage:
    def test_includes_provider_model_mode(self):
        bc = Breadcrumb(
            session_id="abc-12345678",
            written_at_iso="2026-05-03T01:00:00",
            provider_name="ollama",
            model="qwen3.5",
            autonomy_mode="copilot",
            statusline_mode="full",
            recent_turns=[],
        )
        msg = format_recovery_message(bc)
        assert "abc-1234" in msg  # truncated session_id
        assert "ollama" in msg
        assert "qwen3.5" in msg
        assert "copilot" in msg
        assert "full" in msg
        assert "post-compact recovery" in msg

    def test_includes_recent_turns_when_present(self):
        bc = Breadcrumb(
            session_id="abc-1",
            written_at_iso="2026-05-03",
            provider_name="p",
            model="m",
            autonomy_mode="assistant",
            statusline_mode="default",
            recent_turns=[
                {"kind": "user", "text": "What is auth?"},
                {"kind": "agent_text", "text": "Auth is..."},
            ],
        )
        msg = format_recovery_message(bc)
        assert "What is auth?" in msg
        assert "Auth is..." in msg
        assert "recent context" in msg

    def test_caps_at_last_5_turns_in_output(self):
        turns = [{"kind": "user", "text": f"turn {i}"} for i in range(20)]
        bc = Breadcrumb(
            session_id="x",
            written_at_iso="t",
            provider_name=None,
            model=None,
            autonomy_mode="assistant",
            statusline_mode="default",
            recent_turns=turns,
        )
        msg = format_recovery_message(bc)
        # Should show turns 15..19 (last 5), not turn 0
        assert "turn 19" in msg
        assert "turn 15" in msg
        assert "turn 0" not in msg

    def test_omits_recent_section_when_empty(self):
        bc = Breadcrumb(
            session_id="x", written_at_iso="t",
            provider_name=None, model=None,
            autonomy_mode="assistant", statusline_mode="default",
            recent_turns=[],
        )
        msg = format_recovery_message(bc)
        assert "recent context" not in msg


class TestYamlEmitterParser:
    def test_round_trip_preserves_scalars(self):
        data = {"a": "hello", "b": None, "c": "with:colon"}
        out = _parse_yaml(_format_yaml(data))
        assert out["a"] == "hello"
        assert out["b"] is None
        assert out["c"] == "with:colon"

    def test_round_trip_preserves_recent_turns(self):
        data = {"recent_turns": [{"kind": "user", "text": "hi"}]}
        out = _parse_yaml(_format_yaml(data))
        assert out["recent_turns"] == data["recent_turns"]
