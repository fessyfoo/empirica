"""Tests for the POSTFLIGHT pipeline restructure (#95 Issue 3).

Three behavioral guarantees we ship:

  1. Pre-validation (stage 0) failure returns early — NO state mutation.
     Loop stays open, AI can fix context (project-switch) and retry.

  2. _soft_run wraps a stage so any exception becomes a warning, not a
     failure. Successful stages still return their value.

  3. Hard-mutation stages (3-4: close transaction, write reflex) are
     unaffected — they remain hard. The fix only changes how downstream
     stages 5-7 fail.

The full pipeline integration is exercised by existing live POSTFLIGHT
tests; these target the new helpers directly so the test surface is
hermetic and fast.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.workflow_commands import (
    _soft_run,
    _validate_postflight_preconditions,
)


# ─── _validate_postflight_preconditions ─────────────────────────────────────


class TestValidatePostflightPreconditions:
    def test_returns_true_when_session_has_project_id(self):
        # Mock SessionDatabase to return a row with project_id
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = ('valid-project-uuid',)

            ok, err = _validate_postflight_preconditions('session-001')
            assert ok is True
            assert err is None

    def test_returns_false_when_session_row_missing(self):
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = None

            ok, err = _validate_postflight_preconditions('missing-session-uuid')
            assert ok is False
            assert err is not None
            assert 'not found' in err

    def test_returns_false_when_project_id_is_null(self):
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = (None,)

            ok, err = _validate_postflight_preconditions('session-002')
            assert ok is False
            assert 'no project_id' in err
            assert 'project-switch' in err  # actionable hint

    def test_returns_false_when_project_id_is_empty_string(self):
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db = mock_db_cls.return_value
            cursor = mock_db.conn.cursor.return_value
            cursor.fetchone.return_value = ('',)

            ok, err = _validate_postflight_preconditions('session-003')
            assert ok is False
            assert 'no project_id' in err

    def test_fails_open_when_db_unavailable(self):
        # If validation itself can't run, fail-open (return ok=True with a
        # diagnostic message). Downstream soft-run wrappers handle their
        # own errors anyway. The fix should never make POSTFLIGHT *less*
        # available than it was before.
        with patch('empirica.data.session_database.SessionDatabase') as mock_db_cls:
            mock_db_cls.side_effect = OSError("DB inaccessible")

            ok, err = _validate_postflight_preconditions('session-004')
            assert ok is True  # fail-open
            assert err is not None
            assert 'skipped' in err


# ─── _soft_run ───────────────────────────────────────────────────────────────


class TestSoftRun:
    def test_returns_function_value_on_success(self):
        warnings = []
        result = _soft_run("bus_publish", warnings, lambda: 42)
        assert result == 42
        assert warnings == []

    def test_passes_args_and_kwargs_through(self):
        warnings = []
        def fn(a, b, *, c):
            return a + b + c
        result = _soft_run("test_stage", warnings, fn, 1, 2, c=3)
        assert result == 6
        assert warnings == []

    def test_catches_exception_and_appends_warning(self):
        warnings = []
        def boom():
            raise ValueError("explicit failure")

        result = _soft_run("compliance_check", warnings, boom)
        assert result is None
        assert len(warnings) == 1
        w = warnings[0]
        assert w["stage"] == "compliance_check"
        assert w["error_type"] == "ValueError"
        assert w["error"] == "explicit failure"

    def test_multiple_failures_accumulate(self):
        warnings = []
        _soft_run("stage_a", warnings, lambda: 1 / 0)
        _soft_run("stage_b", warnings, lambda: int("not a number"))
        _soft_run("stage_c", warnings, lambda: "fine")  # success

        assert len(warnings) == 2
        assert warnings[0]["stage"] == "stage_a"
        assert warnings[0]["error_type"] == "ZeroDivisionError"
        assert warnings[1]["stage"] == "stage_b"
        assert warnings[1]["error_type"] == "ValueError"

    def test_handles_keyboard_interrupt_does_not_swallow(self):
        # KeyboardInterrupt and SystemExit are special — the broad
        # 'except Exception' here is correct (those derive from
        # BaseException, not Exception). Verify they propagate.
        warnings = []
        def raises_ki():
            raise KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            _soft_run("bus_publish", warnings, raises_ki)
        assert warnings == []  # no warning recorded — exception escaped

    def test_warning_dict_shape_is_serializable(self):
        # Warnings end up in result['warnings'] which is JSON-serialized.
        # Make sure no funky types leak in.
        import json
        warnings = []
        _soft_run("bus_publish", warnings, lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        # Should serialize cleanly
        json.dumps(warnings)
