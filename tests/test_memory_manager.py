"""Tests for empirica.core.memory_manager — the CC memory KV cache layer.

Covers: hot cache updates, auto-section replacement, promotion, demotion,
eviction, stale marker cleanup, and edge cases from the audit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from empirica.core.memory_manager import (
    MEMORY_AUTO_START,
    _collapse_blank_runs,
    _format_auto_section,
    _remove_from_memory_index,
    _replace_auto_section,
    _strip_stale_markers,
    _try_promote_point,
    demote_stale_memories,
    enforce_memory_md_cap,
    promote_eidetic_to_memory,
    update_hot_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MANUAL_CONTENT = """# Empirica Project Memory

## Important Thing

- [Link](some_file.md) — description
"""

AUTO_SECTION = f"""
{MEMORY_AUTO_START}

### Critical (weight > 0.7)
- [0.90] **Finding:** Something important...

---
📊 **5 items ranked** | For deeper context:
- `empirica project-bootstrap --session-id abc12345` (full load + subtasks)
- `git notes show --ref=breadcrumbs HEAD` (session narrative)
"""


def _write_memory_md(memory_dir: Path, content: str) -> Path:
    """Write content to MEMORY.md in the given dir."""
    md = memory_dir / "MEMORY.md"
    md.write_text(content)
    return md


# ---------------------------------------------------------------------------
# _strip_stale_markers
# ---------------------------------------------------------------------------


def test_strip_stale_markers_removes_html_comments():
    content = "before\n<!-- empirica-auto-start -->\nafter"
    assert "empirica-auto-start" not in _strip_stale_markers(content)
    assert "before" in _strip_stale_markers(content)
    assert "after" in _strip_stale_markers(content)


def test_strip_stale_markers_no_op_on_clean_content():
    content = "clean content\nno markers here"
    assert _strip_stale_markers(content) == content


# ---------------------------------------------------------------------------
# _collapse_blank_runs
# ---------------------------------------------------------------------------


def test_collapse_blank_runs():
    content = "a\n\n\n\n\n\nb"
    result = _collapse_blank_runs(content)
    assert result.count("\n") <= 3  # max 2 consecutive blanks


def test_collapse_blank_runs_preserves_single_blanks():
    content = "a\n\nb\n\nc"
    assert _collapse_blank_runs(content) == content


# ---------------------------------------------------------------------------
# _replace_auto_section
# ---------------------------------------------------------------------------


def test_replace_auto_section_append_when_no_auto():
    result = _replace_auto_section(MANUAL_CONTENT, AUTO_SECTION)
    assert MEMORY_AUTO_START in result
    assert "Important Thing" in result


def test_replace_auto_section_replaces_existing():
    existing = MANUAL_CONTENT + AUTO_SECTION
    new_auto = AUTO_SECTION.replace("Something important", "Something new")
    result = _replace_auto_section(existing, new_auto)
    assert "Something new" in result
    assert "Something important" not in result


def test_replace_auto_section_strips_stale_markers():
    existing = MANUAL_CONTENT + "<!-- empirica-auto-start -->\n\n" + AUTO_SECTION
    result = _replace_auto_section(existing, AUTO_SECTION)
    assert "empirica-auto-start" not in result
    assert MEMORY_AUTO_START in result


def test_replace_auto_section_no_excessive_blanks():
    existing = MANUAL_CONTENT + "\n\n\n\n\n\n" + AUTO_SECTION
    result = _replace_auto_section(existing, AUTO_SECTION)
    # No runs of 4+ consecutive newlines
    assert "\n\n\n\n" not in result


# ---------------------------------------------------------------------------
# _format_auto_section
# ---------------------------------------------------------------------------


def test_format_auto_section_empty_artifacts():
    artifacts = {"findings": [], "unknowns": [], "dead_ends": [], "goals": [], "mistakes": []}
    result = _format_auto_section(artifacts, "test-session")
    assert MEMORY_AUTO_START in result
    assert "0 items ranked" in result


def test_format_auto_section_with_findings():
    artifacts = {
        "findings": [{"finding": "Test finding", "impact": 0.8}],
        "unknowns": [],
        "dead_ends": [],
        "goals": [],
        "mistakes": [],
    }
    result = _format_auto_section(artifacts, "test-session")
    assert "Critical" in result
    assert "Test finding" in result


# ---------------------------------------------------------------------------
# update_hot_cache
# ---------------------------------------------------------------------------


def test_update_hot_cache_creates_memory_md(tmp_path):
    memory_dir = tmp_path / ".claude" / "projects" / "test" / "memory"
    memory_dir.mkdir(parents=True)

    with (
        patch("empirica.core.memory_manager.get_memory_md_path", return_value=memory_dir / "MEMORY.md"),
        patch(
            "empirica.core.memory_manager.fetch_ranked_artifacts",
            return_value={
                "findings": [],
                "unknowns": [],
                "dead_ends": [],
                "goals": [],
                "mistakes": [],
            },
        ),
    ):
        result = update_hot_cache("test-session")

    assert result is True
    assert (memory_dir / "MEMORY.md").exists()


def test_update_hot_cache_no_memory_path():
    with patch("empirica.core.memory_manager.get_memory_md_path", return_value=None):
        assert update_hot_cache("test-session") is False


# ---------------------------------------------------------------------------
# promote_eidetic_to_memory
# ---------------------------------------------------------------------------


def test_promote_none_project_id():
    """None project_id should return early, not create project_None_eidetic."""
    result = promote_eidetic_to_memory(project_id=None)
    assert result == []


def test_promote_empty_string_project_id():
    result = promote_eidetic_to_memory(project_id="")
    assert result == []


def test_promote_no_memory_dir():
    with patch("empirica.core.memory_manager.get_memory_dir", return_value=None):
        result = promote_eidetic_to_memory(project_id="test-pid")
    assert result == []


def test_promote_qdrant_import_error():
    """ImportError should be handled gracefully."""
    with (
        patch("empirica.core.memory_manager.get_memory_dir", return_value=Path("/tmp/test")),
        patch("builtins.__import__", side_effect=ImportError("no qdrant")),
    ):
        result = promote_eidetic_to_memory(project_id="test-pid")
    assert result == []


# ---------------------------------------------------------------------------
# _try_promote_point
# ---------------------------------------------------------------------------


def test_try_promote_point_empty_content(tmp_path):
    point = MagicMock()
    point.payload = {"content": "", "confidence": 0.8}
    result = _try_promote_point(point, tmp_path, set())
    assert result is None


def test_try_promote_point_already_promoted(tmp_path):
    point = MagicMock()
    point.payload = {"content": "test content", "confidence": 0.8}
    import hashlib

    h = hashlib.md5(b"test content").hexdigest()[:12]
    result = _try_promote_point(point, tmp_path, {h})
    assert result is None


def test_try_promote_point_creates_file(tmp_path):
    point = MagicMock()
    point.payload = {
        "content": "Important architectural decision about caching",
        "confidence": 0.85,
        "domain": "architecture",
        "confirmation_count": 2,
    }
    promoted_hashes = set()
    result = _try_promote_point(point, tmp_path, promoted_hashes)

    assert result is not None
    assert result.startswith("promoted_")
    assert result.endswith(".md")
    assert (tmp_path / result).exists()

    content = (tmp_path / result).read_text()
    assert "architecture" in content
    assert "0.85" in content
    assert len(promoted_hashes) == 1


# ---------------------------------------------------------------------------
# demote_stale_memories
# ---------------------------------------------------------------------------


def test_demote_stale_memories_only_promotes(tmp_path):
    """Manual memory files should never be demoted."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "manual_note.md").write_text("manual content")
    (memory_dir / "MEMORY.md").write_text("# Memory\n")

    with patch("empirica.core.memory_manager.get_memory_dir", return_value=memory_dir):
        result = demote_stale_memories(stale_days=0)

    assert result == []
    assert (memory_dir / "manual_note.md").exists()


def test_demote_stale_promoted_file(tmp_path):
    """Stale promoted files should be archived."""
    import time

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    promoted = memory_dir / "promoted_old_fact.md"
    promoted.write_text("old content")
    (memory_dir / "MEMORY.md").write_text("# Memory\n- [Old](promoted_old_fact.md)\n")

    # Make the file look old
    import os

    old_time = time.time() - (31 * 86400)
    os.utime(promoted, (old_time, old_time))

    with patch("empirica.core.memory_manager.get_memory_dir", return_value=memory_dir):
        result = demote_stale_memories(stale_days=30)

    assert "promoted_old_fact.md" in result
    assert not promoted.exists()
    assert (memory_dir / "_archive" / "promoted_old_fact.md").exists()


# ---------------------------------------------------------------------------
# enforce_memory_md_cap
# ---------------------------------------------------------------------------


def test_enforce_cap_under_limit(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory_md(memory_dir, "short content\n" * 10)

    with patch("empirica.core.memory_manager.get_memory_md_path", return_value=memory_dir / "MEMORY.md"):
        evicted = enforce_memory_md_cap(max_total_lines=180)
    assert evicted == 0


def test_enforce_cap_strips_blank_runs(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    content = "manual\n" + "\n" * 200 + f"{MEMORY_AUTO_START}\nauto\n"
    _write_memory_md(memory_dir, content)

    with patch("empirica.core.memory_manager.get_memory_md_path", return_value=memory_dir / "MEMORY.md"):
        enforce_memory_md_cap(max_total_lines=180)

    result = (memory_dir / "MEMORY.md").read_text()
    assert "\n\n\n\n" not in result


# ---------------------------------------------------------------------------
# _remove_from_memory_index — substring safety
# ---------------------------------------------------------------------------


def test_remove_from_index_only_removes_links(tmp_path):
    """Should not remove lines that happen to contain the filename as substring."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    content = "# Memory\n\n- [Link](promoted_test.md) — auto\n- Test results show improvement\n"
    _write_memory_md(memory_dir, content)

    _remove_from_memory_index(memory_dir, ["promoted_test.md"])

    result = (memory_dir / "MEMORY.md").read_text()
    assert "Test results show improvement" in result
    assert "(promoted_test.md)" not in result


# ---------------------------------------------------------------------------
# File locking smoke test
# ---------------------------------------------------------------------------


def test_update_hot_cache_uses_lock_file(tmp_path):
    """Verify the lock file is created during update."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory_md(memory_dir, "# Memory\n")

    with (
        patch("empirica.core.memory_manager.get_memory_md_path", return_value=memory_dir / "MEMORY.md"),
        patch(
            "empirica.core.memory_manager.fetch_ranked_artifacts",
            return_value={
                "findings": [],
                "unknowns": [],
                "dead_ends": [],
                "goals": [],
                "mistakes": [],
            },
        ),
    ):
        update_hot_cache("test-session")

    assert (memory_dir / ".memory_lock").exists()
