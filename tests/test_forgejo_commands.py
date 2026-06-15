"""Tests for `empirica forgejo-publish` (forgejo_commands).

All cortex HTTP + git subprocess calls are mocked, so these run on any host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from empirica.cli.command_handlers import forgejo_commands as fc

_UUID = "a0e24049-d159-4834-afcb-930ba64d0e2b"
_FORGEJO_URL = "git@git.getempirica.com:david/empirica-mesh-support.git"
_REFSPECS = [
    "+refs/heads/*:refs/heads/*",
    "+refs/tags/*:refs/tags/*",
    "+refs/notes/empirica/*:refs/notes/empirica/*",
]


def _proc(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(
        yaml.safe_dump({"project_id": _UUID, "name": "empirica-mesh-support"})
    )
    return root


# ── _resolve_project / _resolve_cortex_config ───────────────────────────


def test_resolve_project_reads_yaml(tmp_path):
    root = _make_project(tmp_path)
    pid, name = fc._resolve_project(root)
    assert pid == _UUID
    assert name == "empirica-mesh-support"


def test_resolve_project_missing(tmp_path):
    assert fc._resolve_project(tmp_path / "nope") == (None, None)


# ── _write_deploy_key ───────────────────────────────────────────────────


def test_write_deploy_key_0600_and_newline(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = fc._write_deploy_key(_UUID, "-----BEGIN OPENSSH PRIVATE KEY-----\nx")
    assert p.read_text().endswith("\n")
    assert (p.stat().st_mode & 0o777) == 0o600
    assert p.parent == tmp_path / ".config" / "empirica" / "forgejo-keys"


# ── _set_forgejo_remote: add vs set-url, never touches origin ────────────


def test_set_forgejo_remote_adds_when_absent(tmp_path):
    calls = []

    def fake_git(path, *args, **kw):
        calls.append(args)
        return _proc(stdout="origin\n")  # only origin exists

    with patch.object(fc, "_git", fake_git):
        fc._set_forgejo_remote(tmp_path, _FORGEJO_URL)

    assert ("remote",) in calls  # listed first
    assert ("remote", "add", "forgejo", _FORGEJO_URL) in calls


def test_set_forgejo_remote_updates_when_present(tmp_path):
    calls = []

    def fake_git(path, *args, **kw):
        calls.append(args)
        return _proc(stdout="origin\nforgejo\n")

    with patch.object(fc, "_git", fake_git):
        fc._set_forgejo_remote(tmp_path, _FORGEJO_URL)

    assert ("remote", "set-url", "forgejo", _FORGEJO_URL) in calls
    assert ("remote", "add", "forgejo", _FORGEJO_URL) not in calls


# ── handle_forgejo_publish_command ──────────────────────────────────────


def test_publish_happy_path_pushes_all_refspecs(tmp_path, monkeypatch, capsys):
    root = _make_project(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    pushes = []

    def fake_git(path, *args, key_path=None, **kw):
        if args and args[0] == "push":
            pushes.append((args[2], key_path))
            return _proc(returncode=0)
        return _proc(stdout="origin\n")

    with patch.object(fc, "_resolve_cortex_config", lambda: ("https://cortex.example", "ctx_k")), \
         patch.object(fc, "_forgejo_publish_post", lambda *a, **k: (200, {
             "forgejo_repo_url": _FORGEJO_URL,
             "deploy_key_private": "-----BEGIN OPENSSH PRIVATE KEY-----\nkey",
             "refspecs": _REFSPECS,
         })), \
         patch.object(fc, "_git", fake_git):
        args = SimpleNamespace(path=str(root), output="json", rotate=False, description=None)
        code = fc.handle_forgejo_publish_command(args)

    assert code == 0
    # all three refspecs pushed, each with the written deploy key
    assert {p[0] for p in pushes} == set(_REFSPECS)
    assert all(p[1] is not None for p in pushes)  # key_path passed to push


def test_publish_already_published_no_key_is_ok_with_note(tmp_path, monkeypatch):
    root = _make_project(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with patch.object(fc, "_resolve_cortex_config", lambda: ("https://cortex.example", "ctx_k")), \
         patch.object(fc, "_forgejo_publish_post", lambda *a, **k: (200, {
             "forgejo_repo_url": _FORGEJO_URL, "already_published": True,
         })):
        args = SimpleNamespace(path=str(root), output="json", rotate=False, description=None)
        code = fc.handle_forgejo_publish_command(args)
    assert code == 0  # idempotent re-call is not an error


def test_publish_cortex_error_returns_nonzero(tmp_path, monkeypatch):
    root = _make_project(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with patch.object(fc, "_resolve_cortex_config", lambda: ("https://cortex.example", "ctx_k")), \
         patch.object(fc, "_forgejo_publish_post", lambda *a, **k: (403, {"error": "not owner"})):
        args = SimpleNamespace(path=str(root), output="json", rotate=False, description=None)
        code = fc.handle_forgejo_publish_command(args)
    assert code == 1


def test_publish_no_project_yaml_returns_nonzero(tmp_path):
    args = SimpleNamespace(path=str(tmp_path / "empty"), output="json", rotate=False, description=None)
    code = fc.handle_forgejo_publish_command(args)
    assert code == 1
