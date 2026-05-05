#!/usr/bin/env python3
"""Regenerate docs/SEMANTIC_INDEX.yaml on demand.

The loader at empirica/config/semantic_index_loader.py auto-scans when
the cache is stale, so this script is no longer needed for correctness —
it stays as an explicit way to:
  1. Refresh the committed YAML inventory (for human inspection)
  2. Dry-run the scan to compare cached vs current

Scan logic lives in empirica.core.docs.semantic_scan; this script is a
thin wrapper that writes the result to disk.

Usage:
    python3 scripts/generate_semantic_index.py             # Write to docs/
    python3 scripts/generate_semantic_index.py --dry-run   # Preview
    python3 scripts/generate_semantic_index.py --output .empirica
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the empirica package importable when running from a checkout
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _summarize(entries: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries.values():
        t = entry.get("doc_type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SEMANTIC_INDEX.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--output", default="docs", help="Output directory (default: docs/)")
    parser.add_argument("--root", default=".", help="Project root")
    args = parser.parse_args()

    from empirica.core.docs.semantic_scan import scan_project

    project_root = Path(args.root).resolve()
    entries = scan_project(project_root)

    print(f"Scanned {project_root}")
    print(f"Total entries: {len(entries)}")
    for t, count in sorted(_summarize(entries).items(), key=lambda x: -x[1]):
        print(f"  {t}: {count}")

    if args.dry_run:
        print("\n--- Preview (first 20 entries) ---")
        for path, meta in list(entries.items())[:20]:
            desc = (meta.get("description") or "")[:60]
            print(f"  {path}: {desc}")
        print("\n(dry-run, not written)")
        return 0

    import yaml
    output_dir = project_root / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "SEMANTIC_INDEX.yaml"
    index = {
        "version": "1.0",
        "generated_by": "scripts/generate_semantic_index.py",
        "total_docs_indexed": len(entries),
        "index": entries,
    }
    output_path.write_text(
        yaml.dump(index, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"\nWritten to: {output_path}")
    print("Run 'empirica project-embed' to re-index docs in Qdrant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
