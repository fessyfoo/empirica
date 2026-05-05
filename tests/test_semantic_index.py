"""Tests for the semantic index scanner + loader.

Covers:
- scan_project: produces correct shape, applies first-rule-wins, skips
  patterns + small files
- newest_source_mtime: tracks the freshest source file
- load_semantic_index: cache hit when fresh, live-scan when stale,
  graceful degradation when project_root cannot be resolved
"""

from __future__ import annotations

import time
from pathlib import Path  # used in fixture-style helpers
from unittest.mock import patch

import yaml

from empirica.config.semantic_index_loader import (
    get_semantic_index_path,
    load_semantic_index,
)
from empirica.core.docs.semantic_scan import (
    newest_source_mtime,
    scan_project,
)

# ---------------------------------------------------------------------------
# scan_project
# ---------------------------------------------------------------------------


def _build_minimal_project(root: Path) -> None:
    """Create a tiny project layout with one md + one py file."""
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "OVERVIEW.md").write_text(
        "# Architecture Overview\n\nThis is a sample architecture doc.\n"
        "Padding text to clear the 100-byte minimum file size threshold "
        "so the scanner doesn't skip it.\n",
        encoding="utf-8",
    )
    (root / "empirica" / "core").mkdir(parents=True)
    (root / "empirica" / "core" / "module.py").write_text(
        '"""Sample core module for testing.\n\nLonger second line."""\n'
        "class Foo:\n    pass\n\ndef public_function():\n    pass\n\n"
        "def _private():\n    pass\n# padding to clear 100-byte threshold "
        "so the scanner does not skip this file in tests at all\n",
        encoding="utf-8",
    )


def test_scan_project_returns_md_and_py_entries(tmp_path):
    _build_minimal_project(tmp_path)
    entries = scan_project(tmp_path)
    assert "docs/architecture/OVERVIEW.md" in entries
    assert "empirica/core/module.py" in entries


def test_scan_project_extracts_md_title_as_description(tmp_path):
    _build_minimal_project(tmp_path)
    entries = scan_project(tmp_path)
    md_entry = entries["docs/architecture/OVERVIEW.md"]
    assert md_entry["description"] == "Architecture Overview"


def test_scan_project_extracts_python_docstring_first_line(tmp_path):
    _build_minimal_project(tmp_path)
    entries = scan_project(tmp_path)
    py_entry = entries["empirica/core/module.py"]
    assert py_entry["description"] == "Sample core module for testing."


def test_scan_project_extracts_md_concepts_from_headings(tmp_path):
    _build_minimal_project(tmp_path)
    entries = scan_project(tmp_path)
    md_entry = entries["docs/architecture/OVERVIEW.md"]
    assert "Architecture Overview" in md_entry["concepts"]


def test_scan_project_extracts_python_concepts_from_classes_and_public_funcs(tmp_path):
    _build_minimal_project(tmp_path)
    entries = scan_project(tmp_path)
    py_entry = entries["empirica/core/module.py"]
    concepts = py_entry["concepts"]
    assert "Foo" in concepts
    assert "public_function" in concepts
    # Private functions excluded
    assert "_private" not in concepts


def test_scan_project_assigns_doc_type_per_rule(tmp_path):
    _build_minimal_project(tmp_path)
    entries = scan_project(tmp_path)
    assert entries["docs/architecture/OVERVIEW.md"]["doc_type"] == "architecture"
    assert entries["empirica/core/module.py"]["doc_type"] == "core-module"


def test_scan_project_skips_init_files(tmp_path):
    (tmp_path / "empirica" / "core").mkdir(parents=True)
    (tmp_path / "empirica" / "core" / "__init__.py").write_text(
        '"""Package init."""\n# padding ' + ("x" * 100), encoding="utf-8"
    )
    (tmp_path / "empirica" / "core" / "real.py").write_text(
        '"""Real module."""\n# padding ' + ("x" * 100), encoding="utf-8"
    )
    entries = scan_project(tmp_path)
    assert "empirica/core/__init__.py" not in entries
    assert "empirica/core/real.py" in entries


def test_scan_project_skips_files_below_min_size(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "tiny.md").write_text("# T", encoding="utf-8")
    entries = scan_project(tmp_path)
    assert "docs/tiny.md" not in entries


def test_scan_project_skips_pycache_and_build(tmp_path):
    (tmp_path / "empirica" / "core" / "__pycache__").mkdir(parents=True)
    (tmp_path / "empirica" / "core" / "__pycache__" / "stale.cpython.pyc").write_bytes(
        b"x" * 200
    )
    (tmp_path / "build" / "lib" / "empirica" / "core").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "empirica" / "core" / "shadowed.py").write_text(
        '"""Shadowed."""\n# padding ' + ("x" * 100), encoding="utf-8"
    )
    entries = scan_project(tmp_path)
    assert not any("__pycache__" in p for p in entries)
    assert not any(p.startswith("build/") for p in entries)


# ---------------------------------------------------------------------------
# newest_source_mtime
# ---------------------------------------------------------------------------


def test_newest_source_mtime_tracks_freshest_file(tmp_path):
    _build_minimal_project(tmp_path)
    older_mtime = newest_source_mtime(tmp_path)

    time.sleep(0.05)
    new_file = tmp_path / "docs" / "architecture" / "NEW.md"
    new_file.write_text("# New\n\nFreshly added doc with enough text to pass minimum.\n",
                         encoding="utf-8")

    newer_mtime = newest_source_mtime(tmp_path)
    assert newer_mtime > older_mtime


def test_newest_source_mtime_returns_zero_for_empty_project(tmp_path):
    assert newest_source_mtime(tmp_path) == 0.0


# ---------------------------------------------------------------------------
# load_semantic_index — cache + staleness
# ---------------------------------------------------------------------------


def _patch_git_root(tmp_path):
    """Patch get_git_root to return tmp_path so the loader resolves there."""
    return patch(
        "empirica.config.path_resolver.get_git_root",
        return_value=str(tmp_path),
    )


def test_load_semantic_index_live_scan_when_no_cache(tmp_path):
    _build_minimal_project(tmp_path)
    with _patch_git_root(tmp_path):
        idx = load_semantic_index(write_back=False)
    assert idx is not None
    assert idx["total_docs_indexed"] >= 2
    paths = idx["index"]
    assert "empirica/core/module.py" in paths


def test_load_semantic_index_uses_fresh_cache(tmp_path):
    """Cache fresher than newest source → returns cache verbatim."""
    _build_minimal_project(tmp_path)

    # Hand-craft a "fresh" cache that's newer than all source files
    cache_dir = tmp_path / "docs"
    cache_path = cache_dir / "SEMANTIC_INDEX.yaml"
    sentinel = {
        "version": "1.0",
        "generated_by": "test-fixture",
        "total_docs_indexed": 999,
        "index": {"sentinel.md": {"tags": ["test"], "doc_type": "fixture"}},
    }
    cache_path.write_text(yaml.dump(sentinel), encoding="utf-8")
    # Bump cache mtime to be definitively newer than sources
    future = time.time() + 100
    import os
    os.utime(cache_path, (future, future))

    with _patch_git_root(tmp_path):
        idx = load_semantic_index(write_back=False)
    assert idx is not None
    assert idx["generated_by"] == "test-fixture"
    assert idx["total_docs_indexed"] == 999


def test_load_semantic_index_rescans_when_cache_stale(tmp_path):
    """Cache older than newest source → live scan kicks in."""
    _build_minimal_project(tmp_path)

    cache_dir = tmp_path / "docs"
    cache_path = cache_dir / "SEMANTIC_INDEX.yaml"
    cache_path.write_text(
        yaml.dump({
            "version": "1.0",
            "generated_by": "stale-fixture",
            "total_docs_indexed": 1,
            "index": {"only-stale-entry.md": {"tags": []}},
        }),
        encoding="utf-8",
    )
    # Backdate cache to be older than all sources
    past = time.time() - 10000
    import os
    os.utime(cache_path, (past, past))

    with _patch_git_root(tmp_path):
        idx = load_semantic_index(write_back=False)
    assert idx is not None
    # Should reflect live scan, not the stale fixture
    assert idx["generated_by"] != "stale-fixture"
    assert "empirica/core/module.py" in idx["index"]


def test_load_semantic_index_writes_back_after_live_scan(tmp_path):
    _build_minimal_project(tmp_path)
    with _patch_git_root(tmp_path):
        idx = load_semantic_index(write_back=True)
    assert idx is not None

    cache_path = tmp_path / "docs" / "SEMANTIC_INDEX.yaml"
    assert cache_path.exists()
    cached = yaml.safe_load(cache_path.read_text(encoding="utf-8"))
    assert cached["total_docs_indexed"] == idx["total_docs_indexed"]


def test_load_semantic_index_force_scan_bypasses_cache(tmp_path):
    """force_scan=True returns live scan even when cache would be valid."""
    _build_minimal_project(tmp_path)

    cache_path = tmp_path / "docs" / "SEMANTIC_INDEX.yaml"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        yaml.dump({"version": "1.0", "generated_by": "stale", "total_docs_indexed": 1, "index": {}}),
        encoding="utf-8",
    )
    future = time.time() + 100
    import os
    os.utime(cache_path, (future, future))

    with _patch_git_root(tmp_path):
        idx = load_semantic_index(force_scan=True, write_back=False)
    assert idx is not None
    assert idx["generated_by"] != "stale"


def test_load_semantic_index_returns_none_outside_git_repo():
    with patch("empirica.config.path_resolver.get_git_root", return_value=None):
        assert load_semantic_index() is None


def test_get_semantic_index_path_does_not_trigger_scan(tmp_path):
    """Path lookup should be O(1), not invoke the scanner."""
    _build_minimal_project(tmp_path)
    # No cache file exists yet
    with _patch_git_root(tmp_path):
        path = get_semantic_index_path()
    assert path is None  # No cache present, no auto-creation
    # Verify still nothing on disk
    assert not (tmp_path / "docs" / "SEMANTIC_INDEX.yaml").exists()
