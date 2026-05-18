"""Tests for the persistent listener service install (prop_flrtxxn32japbazq).

Covers:
  - OS detection / backend resolution
  - Systemd unit template rendering
  - Launchd plist template rendering
  - install / uninstall / status idempotency
  - is_listener_running fail-safe (always returns bool, never raises)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from empirica.core.loop_scheduler.persistent_listener import (
    ListenerServiceUnavailable,
    PersistentListenerService,
    _safe,
    _unit_name,
    is_listener_running,
)

# ─── Helper sanitization ────────────────────────────────────────────────


def test_safe_sanitizes_special_chars():
    assert _safe("cortex") == "cortex"
    assert _safe("empirica-workspace") == "empirica-workspace"
    assert _safe("foo/bar") == "foo-bar"
    assert _safe("foo bar") == "foo-bar"
    assert _safe("") == "default"


def test_unit_name_format():
    assert _unit_name("cortex") == "empirica-listener-cortex"
    assert _unit_name("foo/bar") == "empirica-listener-foo-bar"


# ─── Backend detection ──────────────────────────────────────────────────


def test_backend_unavailable_when_no_systemd_no_launchd():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=False):
        service = PersistentListenerService()
    assert service.backend == "unavailable"


def test_backend_systemd_when_only_systemd_available():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True):
        service = PersistentListenerService()
    assert service.backend == "systemd"


def test_backend_launchd_when_only_launchd_available():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener.sys") as mock_sys:
        mock_sys.platform = "darwin"
        service = PersistentListenerService()
    assert service.backend == "launchd"


def test_empirica_bin_resolves_to_absolute_path():
    with patch("empirica.core.loop_scheduler.persistent_listener.shutil.which",
               return_value="/usr/local/bin/empirica"), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True):
        service = PersistentListenerService()
    assert service.empirica_bin == "/usr/local/bin/empirica"


def test_empirica_bin_explicit_override():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True):
        service = PersistentListenerService(empirica_bin="/custom/path/empirica")
    assert service.empirica_bin == "/custom/path/empirica"


# ─── Path resolution ────────────────────────────────────────────────────


def test_unit_path_systemd(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False):
        service = PersistentListenerService(empirica_bin="empirica")
    path = service.unit_path("cortex")
    assert path is not None
    assert path.name == "empirica-listener-cortex.service"
    assert ".config/systemd/user" in str(path)


def test_unit_path_unavailable_returns_none():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False):
        service = PersistentListenerService(empirica_bin="empirica")
    assert service.unit_path("cortex") is None


# ─── Install ────────────────────────────────────────────────────────────


def test_install_raises_when_unavailable():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False):
        service = PersistentListenerService(empirica_bin="empirica")
    import pytest
    with pytest.raises(ListenerServiceUnavailable):
        service.install("cortex")


def test_install_systemd_writes_unit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fake_run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener._systemctl",
               side_effect=lambda *a, **kw: fake_run(*a, **kw)):
        service = PersistentListenerService(empirica_bin="/path/to/empirica")
        unit_file = service.install("cortex")

    assert unit_file.exists()
    content = unit_file.read_text()
    assert "Description=Empirica persistent listener — cortex" in content
    assert "ExecStart=/path/to/empirica loop listen --instance cortex" in content
    assert "Restart=always" in content
    # Systemctl was called: daemon-reload + enable --now
    assert fake_run.call_count >= 2


def test_install_systemd_log_path_in_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener._systemctl",
               return_value=subprocess.CompletedProcess([], 0, "", "")):
        service = PersistentListenerService(empirica_bin="empirica")
        unit_file = service.install("cortex")
    content = unit_file.read_text()
    assert "/.empirica/logs/listener-cortex.log" in content


def test_install_launchd_writes_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.sys") as mock_sys, \
         patch("empirica.core.loop_scheduler.persistent_listener._launchctl",
               return_value=subprocess.CompletedProcess([], 0, "", "")):
        mock_sys.platform = "darwin"
        service = PersistentListenerService(empirica_bin="/path/to/empirica")
        plist_file = service.install("cortex")

    assert plist_file.exists()
    content = plist_file.read_text()
    assert "<key>Label</key>" in content
    assert "<string>com.empirica.listener.cortex</string>" in content
    assert "<string>/path/to/empirica</string>" in content
    assert "loop" in content
    assert "<key>KeepAlive</key>" in content
    assert "<key>RunAtLoad</key>" in content


# ─── Uninstall ──────────────────────────────────────────────────────────


def test_uninstall_returns_false_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False):
        service = PersistentListenerService(empirica_bin="empirica")
        result = service.uninstall("cortex")
    assert result is False


def test_uninstall_removes_systemd_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Pre-create a unit file
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit_file = unit_dir / "empirica-listener-cortex.service"
    unit_file.write_text("[Unit]")

    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener._systemctl",
               return_value=subprocess.CompletedProcess([], 0, "", "")):
        service = PersistentListenerService(empirica_bin="empirica")
        result = service.uninstall("cortex")

    assert result is True
    assert not unit_file.exists()


# ─── Status ─────────────────────────────────────────────────────────────


def test_status_unavailable_backend():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False):
        service = PersistentListenerService(empirica_bin="empirica")
    status = service.status("cortex")
    assert status.backend == "unavailable"
    assert status.installed is False
    assert status.active is False


def test_status_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False):
        service = PersistentListenerService(empirica_bin="empirica")
    status = service.status("cortex")
    assert status.backend == "systemd"
    assert status.installed is False
    assert status.active is False


def test_status_installed_and_active(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "empirica-listener-cortex.service").write_text("[Unit]")

    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener._systemctl",
               return_value=subprocess.CompletedProcess([], 0, "active\n", "")):
        service = PersistentListenerService(empirica_bin="empirica")
        status = service.status("cortex")

    assert status.installed is True
    assert status.active is True


def test_status_installed_but_inactive(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "empirica-listener-cortex.service").write_text("[Unit]")

    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener._systemctl",
               return_value=subprocess.CompletedProcess([], 3, "inactive\n", "")):
        service = PersistentListenerService(empirica_bin="empirica")
        status = service.status("cortex")

    assert status.installed is True
    assert status.active is False


# ─── Fail-safe wrappers ────────────────────────────────────────────────


def test_is_listener_running_never_raises_on_error():
    """The module-level is_listener_running is used by hooks — must never raise."""
    with patch("empirica.core.loop_scheduler.persistent_listener.PersistentListenerService",
               side_effect=RuntimeError("simulated failure")):
        result = is_listener_running("cortex")
    assert result is False


def test_is_listener_running_returns_true_when_active(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "empirica-listener-cortex.service").write_text("[Unit]")

    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available",
               return_value=True), \
         patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available",
               return_value=False), \
         patch("empirica.core.loop_scheduler.persistent_listener._systemctl",
               return_value=subprocess.CompletedProcess([], 0, "active\n", "")):
        assert is_listener_running("cortex") is True
