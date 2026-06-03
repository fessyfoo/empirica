"""Tests for the project-init chicken-egg recovery paths.

Per ecodex prop_jnqs2l4l: sandboxed harnesses pre-mount .git/ read-only,
so `git init` fails with EACCES. Two recovery paths are wired into
`_ensure_git_root`:

1. `--project-id` shortcut — if the caller already has a workspace identity,
   skip git init and use cwd as the anchor.
2. Clear actionable error on git init failure — JSON shape carries
   `likely_cause` + `recovery` hints; human shape prints labeled recovery
   options.
"""

from __future__ import annotations

import io
import json
import subprocess
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch

from empirica.cli.command_handlers.project_init import (
    _ensure_git_root,
    _report_git_init_failure,
)


def test_project_id_shortcut_skips_git_init(tmp_path, monkeypatch):
    """--project-id supplied + no git root → return cwd, never call git init."""
    monkeypatch.chdir(tmp_path)
    args = Namespace(project_id="abc-123-uuid")

    with (
        patch("empirica.config.path_resolver.get_git_root", return_value=None),
        patch("subprocess.run") as run_mock,
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _ensure_git_root(interactive=False, output_format="human", args=args)

    assert result == tmp_path
    run_mock.assert_not_called()
    assert "using cwd as anchor" in buf.getvalue()


def test_project_id_shortcut_silent_under_json(tmp_path, monkeypatch):
    """--project-id + JSON output → return cwd silently (no stdout noise)."""
    monkeypatch.chdir(tmp_path)
    args = Namespace(project_id="abc-123-uuid")

    with (
        patch("empirica.config.path_resolver.get_git_root", return_value=None),
        patch("subprocess.run") as run_mock,
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _ensure_git_root(interactive=False, output_format="json", args=args)

    assert result == tmp_path
    run_mock.assert_not_called()
    assert buf.getvalue() == ""


def test_git_init_failure_emits_recovery_hints_json(tmp_path, monkeypatch):
    """git init fails + JSON output → structured error with recovery list."""
    monkeypatch.chdir(tmp_path)
    args = Namespace(project_id=None)

    exc = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "init"],
        stderr=b"fatal: cannot mkdir '.git': Read-only file system",
    )

    with (
        patch("empirica.config.path_resolver.get_git_root", return_value=None),
        patch("subprocess.run", side_effect=exc),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _ensure_git_root(interactive=False, output_format="json", args=args)

    assert result is None
    payload = json.loads(buf.getvalue())
    assert payload["ok"] is False
    assert payload["error"] == "git init failed"
    assert "Read-only" in payload["stderr"]
    assert any("--project-id" in r for r in payload["recovery"])


def test_git_init_failure_human_output_lists_recovery(tmp_path, monkeypatch):
    """git init fails + human output → labeled recovery options on stdout."""
    monkeypatch.chdir(tmp_path)
    args = Namespace(project_id=None)

    exc = subprocess.CalledProcessError(returncode=128, cmd=["git", "init"], stderr=b"")

    with (
        patch("empirica.config.path_resolver.get_git_root", return_value=None),
        patch("subprocess.run", side_effect=exc),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _ensure_git_root(interactive=False, output_format="human", args=args)

    assert result is None
    out = buf.getvalue()
    assert "Failed to initialize git repository" in out
    assert "--project-id" in out
    assert "Recovery options" in out


def test_readonly_git_mount_detected_in_failure_report(tmp_path, monkeypatch):
    """If .git/ exists but is read-only, the likely_cause field flags it."""
    monkeypatch.chdir(tmp_path)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    git_dir.chmod(0o555)  # read + execute, no write

    try:
        exc = subprocess.CalledProcessError(returncode=128, cmd=["git", "init"], stderr=b"")
        buf = io.StringIO()
        with redirect_stdout(buf):
            _report_git_init_failure(exc, output_format="json")
        payload = json.loads(buf.getvalue())
        assert "read-only" in payload["likely_cause"].lower()
    finally:
        git_dir.chmod(0o755)  # restore so tmp_path cleanup works


def test_no_args_passed_falls_back_to_init_attempt(tmp_path, monkeypatch):
    """Backward-compat: caller that doesn't pass args still hits the init path."""
    monkeypatch.chdir(tmp_path)

    with (
        patch("empirica.config.path_resolver.get_git_root", side_effect=[None, tmp_path]),
        patch("subprocess.run") as run_mock,
    ):
        result = _ensure_git_root(interactive=False, output_format="json", args=None)

    assert result == tmp_path
    run_mock.assert_called_once()
