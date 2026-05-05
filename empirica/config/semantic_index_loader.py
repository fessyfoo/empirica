#!/usr/bin/env python3
"""Semantic Index Loader — live-scan with cache fallback.

Returns the per-file metadata index that project-embed and doc_planner
consume. The cached YAML at docs/SEMANTIC_INDEX.yaml is treated as a
performance hint, not a source of truth: if any source file is newer
than the cache, the loader falls through to a live scan and (best-effort)
writes the fresh result back so subsequent reads are fast again.

Resolution priority (in order):
  1. Live scan when force_scan=True
  2. Cached YAML at <project>/docs/SEMANTIC_INDEX.yaml — if mtime ≥ newest source mtime
  3. Cached YAML at <project>/.empirica/SEMANTIC_INDEX.yaml — same staleness check
  4. Live scan + cache write-back

Usage:
    from empirica.config.semantic_index_loader import load_semantic_index
    index = load_semantic_index()
    docs = (index or {}).get("index", {})
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def load_semantic_index(
    project_root: str | None = None,
    *,
    force_scan: bool = False,
    write_back: bool = True,
) -> dict[str, Any] | None:
    """Load semantic index, scanning live when cache is stale or missing.

    Args:
        project_root: Project root directory. Defaults to git root.
        force_scan: If True, skip cache and rescan.
        write_back: If True, write a refreshed cache after a live scan.

    Returns:
        Index dict with keys {version, generated_by, total_docs_indexed,
        index} — or None when project_root cannot be resolved.
    """
    project_root = _resolve_project_root(project_root)
    if project_root is None:
        return None

    cache_path = _existing_cache_path(project_root)

    if not force_scan and cache_path is not None and not _is_cache_stale(cache_path, project_root):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = yaml.safe_load(f)
            if cached:
                logger.debug(f"semantic_index: cache hit at {cache_path}")
                return cached
        except Exception as e:
            logger.warning(f"semantic_index: failed to read cache {cache_path}: {e}")

    # Live scan
    try:
        from empirica.core.docs.semantic_scan import scan_project
        entries = scan_project(project_root)
    except Exception as e:
        logger.warning(f"semantic_index: live scan failed: {e}")
        # Last-ditch fallback to whatever the cache holds, even if stale,
        # rather than returning None — stale data is more useful than no data.
        if cache_path is not None:
            try:
                with open(cache_path, encoding="utf-8") as f:
                    return yaml.safe_load(f)
            except Exception as fallback_err:
                logger.debug(f"semantic_index: stale-cache fallback also failed: {fallback_err}")
        return None

    index = {
        "version": "1.0",
        "generated_by": "empirica.config.semantic_index_loader",
        "total_docs_indexed": len(entries),
        "index": entries,
    }

    if write_back:
        target = cache_path or (project_root / "docs" / "SEMANTIC_INDEX.yaml")
        _try_write_cache(target, index)

    logger.debug(f"semantic_index: live-scanned {len(entries)} entries from {project_root}")
    return index


def get_semantic_index_path(project_root: str | None = None) -> Path | None:
    """Path to the cached SEMANTIC_INDEX.yaml if one exists. None otherwise.

    Does NOT trigger a scan — purely a path lookup. Callers wanting fresh
    data should use load_semantic_index() instead.
    """
    resolved = _resolve_project_root(project_root)
    if resolved is None:
        return None
    return _existing_cache_path(resolved)


# --- internals ---


def _resolve_project_root(project_root: str | None) -> Path | None:
    if project_root is not None:
        return Path(project_root)
    try:
        from empirica.config.path_resolver import get_git_root
        git_root = get_git_root()
    except Exception as e:
        logger.debug(f"semantic_index: get_git_root raised: {e}")
        return None
    if not git_root:
        logger.debug("semantic_index: not in a git repo")
        return None
    return Path(git_root)


def _existing_cache_path(project_root: Path) -> Path | None:
    for candidate in (
        project_root / "docs" / "SEMANTIC_INDEX.yaml",
        project_root / ".empirica" / "SEMANTIC_INDEX.yaml",
    ):
        if candidate.exists():
            return candidate
    return None


def _is_cache_stale(cache_path: Path, project_root: Path) -> bool:
    """Cache is stale if any source file is newer than the cache mtime."""
    try:
        cache_mtime = cache_path.stat().st_mtime
    except OSError:
        return True
    try:
        from empirica.core.docs.semantic_scan import newest_source_mtime
        newest = newest_source_mtime(project_root)
    except Exception as e:
        logger.debug(f"semantic_index: newest-source check failed: {e}")
        return False  # On error, prefer cache — better than crashing on every load.
    return newest > cache_mtime


def _try_write_cache(target: Path, index: dict[str, Any]) -> None:
    """Best-effort write — never raises. Stale cache survival is fine."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.dump(
            index, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
        target.write_text(text, encoding="utf-8")
        logger.debug(f"semantic_index: wrote refreshed cache to {target}")
    except Exception as e:
        logger.debug(f"semantic_index: cache write skipped: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    print("🔍 Semantic Index Loader Debug\n")
    idx = load_semantic_index()
    if idx:
        print("✅ Index resolved")
        print(f"   Generated by: {idx.get('generated_by', '?')}")
        print(f"   Total entries: {idx.get('total_docs_indexed', 0)}")
        print(f"   Distinct paths: {len(idx.get('index', {}))}")
    else:
        print("❌ No index resolved (not in a git repo?)")
    p = get_semantic_index_path()
    print(f"\n📍 Cache path: {p}")
