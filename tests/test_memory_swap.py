"""Tests for the memory directory swap that follows active transactions
across CWD mismatches.

The swap module backs up the harness-CWD project's auto-memory directory
contents and replaces them with the active-transaction project's contents,
so Claude Code's auto-memory loader (which is wired to harness CWD at
session start) reads the right project's memory.

KNOWN_ISSUES 11.28.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add empirica src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from empirica.utils.memory_swap import (
    BACKUP_SUBDIR,
    _claude_memory_dir,
    is_swap_active,
    maybe_swap_for_active_transaction,
    read_manifest,
    restore_memory,
    swap_memory,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect HOME to a tmp dir so memory dirs don't touch real ones.

    Path.home() honors $HOME at call time, so setenv alone is sufficient.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def harness_project(tmp_path):
    """A fake harness CWD project with its own memory dir."""
    project = tmp_path / "harness_project"
    project.mkdir()
    return project


@pytest.fixture
def active_project(tmp_path):
    """A fake active-transaction project with its own memory dir."""
    project = tmp_path / "active_project"
    project.mkdir()
    return project


def _seed_memory_dir(project_path: Path, files: dict[str, str], fake_home: Path):
    """Create a Claude memory dir for the project and seed it with files."""
    memory_dir = _claude_memory_dir(project_path)
    memory_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (memory_dir / name).write_text(content)
    return memory_dir


# ---------------------------------------------------------------------------
# Path computation
# ---------------------------------------------------------------------------


class TestClaudeMemoryDir:
    def test_path_encoding(self, tmp_path, fake_home):
        project = tmp_path / "some" / "project"
        project.mkdir(parents=True)
        memory_dir = _claude_memory_dir(project)
        # Claude Code maps absolute paths by replacing / with -
        expected_key = str(project.resolve()).replace("/", "-")
        assert memory_dir == fake_home / ".claude" / "projects" / expected_key / "memory"


# ---------------------------------------------------------------------------
# swap_memory
# ---------------------------------------------------------------------------


class TestSwapMemory:
    def test_swap_copies_source_to_target(self, fake_home, harness_project, active_project):
        """After swap, target memory dir contains source's files."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "harness original"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "active content", "extra.md": "extra"}, fake_home)

        result = swap_memory(harness_project, active_project)
        assert result["ok"] is True
        assert result["action"] == "swapped"

        target_memory = _claude_memory_dir(harness_project)
        assert (target_memory / "MEMORY.md").read_text() == "active content"
        assert (target_memory / "extra.md").read_text() == "extra"

    def test_swap_backs_up_originals(self, fake_home, harness_project, active_project):
        """Original target files end up in the backup subdir."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "harness original"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "active content"}, fake_home)

        swap_memory(harness_project, active_project)

        target_memory = _claude_memory_dir(harness_project)
        backup = target_memory / BACKUP_SUBDIR
        assert (backup / "MEMORY.md").read_text() == "harness original"

    def test_swap_writes_manifest(self, fake_home, harness_project, active_project):
        """Manifest file records the swap details."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "y"}, fake_home)

        swap_memory(
            harness_project,
            active_project,
            claude_session_id="abc-123",
            transaction_id="tx-456",
        )

        manifest = read_manifest(harness_project)
        assert manifest is not None
        assert manifest["source_project"] == str(active_project.resolve())
        assert manifest["claude_session_id"] == "abc-123"
        assert manifest["transaction_id"] == "tx-456"
        assert "MEMORY.md" in manifest["copied_files"]

    def test_swap_noop_when_paths_equal(self, fake_home, harness_project):
        """Swapping a project against itself does nothing."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        result = swap_memory(harness_project, harness_project)
        assert result["action"] == "noop"
        assert not is_swap_active(harness_project)

    def test_swap_noop_when_source_missing(self, fake_home, harness_project, active_project):
        """If the source memory dir doesn't exist, swap is skipped."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        # active_project memory dir intentionally not created
        result = swap_memory(harness_project, active_project)
        assert result["ok"] is False
        assert result["action"] == "skip"

    def test_swap_idempotent(self, fake_home, harness_project, active_project):
        """Calling swap twice with the same source returns already_active."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "y"}, fake_home)

        first = swap_memory(harness_project, active_project)
        assert first["action"] == "swapped"

        second = swap_memory(harness_project, active_project)
        assert second["action"] == "already_active"

    def test_swap_replaces_existing_swap(self, fake_home, tmp_path, harness_project):
        """Swapping with a different source replaces the old swap, not layers it."""
        active_a = tmp_path / "active_a"
        active_a.mkdir()
        active_b = tmp_path / "active_b"
        active_b.mkdir()
        _seed_memory_dir(harness_project, {"MEMORY.md": "harness"}, fake_home)
        _seed_memory_dir(active_a, {"MEMORY.md": "from_a"}, fake_home)
        _seed_memory_dir(active_b, {"MEMORY.md": "from_b"}, fake_home)

        swap_memory(harness_project, active_a)
        swap_memory(harness_project, active_b)

        target_memory = _claude_memory_dir(harness_project)
        assert (target_memory / "MEMORY.md").read_text() == "from_b"
        manifest = read_manifest(harness_project)
        assert manifest["source_project"] == str(active_b.resolve())


# ---------------------------------------------------------------------------
# restore_memory
# ---------------------------------------------------------------------------


class TestRestoreMemory:
    def test_restore_returns_originals(self, fake_home, harness_project, active_project):
        """After restore, target dir has the original harness files back."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "harness original", "h.md": "more"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "active content"}, fake_home)

        swap_memory(harness_project, active_project)
        restore_memory(harness_project)

        target_memory = _claude_memory_dir(harness_project)
        assert (target_memory / "MEMORY.md").read_text() == "harness original"
        assert (target_memory / "h.md").read_text() == "more"

    def test_restore_removes_swapped_in_files(self, fake_home, harness_project, active_project):
        """Restore removes files that were copied in from source but never existed in target."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "harness"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "active", "extra.md": "extra"}, fake_home)

        swap_memory(harness_project, active_project)
        target_memory = _claude_memory_dir(harness_project)
        assert (target_memory / "extra.md").exists()  # was swapped in

        restore_memory(harness_project)
        assert not (target_memory / "extra.md").exists()  # cleaned up

    def test_restore_clears_manifest(self, fake_home, harness_project, active_project):
        """After restore, the manifest file is gone."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "y"}, fake_home)

        swap_memory(harness_project, active_project)
        assert is_swap_active(harness_project)

        restore_memory(harness_project)
        assert not is_swap_active(harness_project)

    def test_restore_clears_backup_dir(self, fake_home, harness_project, active_project):
        """After restore, the backup subdir is gone."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "y"}, fake_home)

        swap_memory(harness_project, active_project)
        target_memory = _claude_memory_dir(harness_project)
        assert (target_memory / BACKUP_SUBDIR).exists()

        restore_memory(harness_project)
        assert not (target_memory / BACKUP_SUBDIR).exists()

    def test_restore_noop_when_no_swap(self, fake_home, harness_project):
        """Restoring an unswapped dir is a no-op."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        result = restore_memory(harness_project)
        assert result["action"] == "noop"


# ---------------------------------------------------------------------------
# Round-trip — swap and restore preserve content exactly
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_swap_then_restore_preserves_content(self, fake_home, harness_project, active_project):
        """Round-trip: original content is byte-identical after swap+restore."""
        original = {"MEMORY.md": "first\nsecond\nthird\n", "ref.md": "data"}
        _seed_memory_dir(harness_project, original, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "active"}, fake_home)

        swap_memory(harness_project, active_project)
        restore_memory(harness_project)

        target_memory = _claude_memory_dir(harness_project)
        for name, content in original.items():
            assert (target_memory / name).read_text() == content

    def test_swap_with_subdirectories(self, fake_home, harness_project, active_project):
        """Swap handles nested directories in the memory dir."""
        target_memory = _claude_memory_dir(harness_project)
        target_memory.mkdir(parents=True)
        sub = target_memory / "sub"
        sub.mkdir()
        (sub / "nested.md").write_text("nested content")
        (target_memory / "MEMORY.md").write_text("top")

        source_memory = _claude_memory_dir(active_project)
        source_memory.mkdir(parents=True)
        (source_memory / "MEMORY.md").write_text("source top")

        swap_memory(harness_project, active_project)
        # Swap removed the nested dir from target
        assert not sub.exists()
        # Restore brings it back
        restore_memory(harness_project)
        assert sub.exists()
        assert (sub / "nested.md").read_text() == "nested content"


# ---------------------------------------------------------------------------
# maybe_swap_for_active_transaction (hook entry point)
# ---------------------------------------------------------------------------


class TestMaybeSwapForActiveTransaction:
    def test_swaps_when_context_has_project(self, fake_home, harness_project, active_project, monkeypatch):
        """When InstanceResolver returns a project_path, swap fires."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "harness"}, fake_home)
        _seed_memory_dir(active_project, {"MEMORY.md": "active"}, fake_home)

        monkeypatch.chdir(harness_project)

        fake_context = {
            "project_path": str(active_project),
            "transaction_id": "tx-1",
        }
        with patch("empirica.utils.session_resolver.InstanceResolver.context", return_value=fake_context):
            result = maybe_swap_for_active_transaction(claude_session_id="cs-1")

        assert result["ok"] is True
        assert result["action"] == "swapped"
        target_memory = _claude_memory_dir(harness_project)
        assert (target_memory / "MEMORY.md").read_text() == "active"

    def test_noop_when_no_active_project(self, fake_home, harness_project, monkeypatch):
        """When InstanceResolver has no project_path, no swap happens."""
        _seed_memory_dir(harness_project, {"MEMORY.md": "x"}, fake_home)
        monkeypatch.chdir(harness_project)

        with patch("empirica.utils.session_resolver.InstanceResolver.context", return_value={}):
            result = maybe_swap_for_active_transaction()

        assert result["action"] == "noop"
        assert not is_swap_active(harness_project)
