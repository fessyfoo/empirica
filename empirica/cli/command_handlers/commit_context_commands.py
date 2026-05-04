"""Commit-context CLI: aggregates git notes anchored to commit(s).

Surfaces the per-commit artifact set that already lives in refs/notes/empirica/*.
Each first-class artifact (finding/decision/dead-end/mistake/unknown/assumption/
goal/cascade) is stored as its own notes ref and anchored to the commit that
existed when it was logged. This command makes that linkage queryable in one
fetch instead of git-notes archaeology.

Query forms:
    empirica commit-context <sha>            # single commit
    empirica commit-context HEAD~5..HEAD     # rev range
    empirica commit-context --since 2026-04-01 [--until ...]
    empirica commit-context --session <id>   # all commits in an Empirica session
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from empirica.config.path_resolver import get_git_root

logger = logging.getLogger(__name__)

# First-class artifact namespaces under refs/notes/empirica/<type>/<uuid>.
# Excludes session/handoff/messages/signatures/tasks/test* — those have
# different ref structures and aren't per-commit artifacts.
ARTIFACT_NAMESPACES = (
    "findings", "decisions", "dead_ends", "mistakes",
    "unknowns", "assumptions", "goals", "cascades",
)


def _git(workspace: Path, *args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command and return (rc, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=workspace,
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _list_refs(workspace: Path) -> list[str]:
    """Enumerate all refs under refs/notes/empirica/<type>/ for tracked types."""
    refs: list[str] = []
    for ns in ARTIFACT_NAMESPACES:
        rc, out, _ = _git(workspace, "for-each-ref", "--format=%(refname)",
                          f"refs/notes/empirica/{ns}/")
        if rc == 0:
            refs.extend(r for r in out.strip().split("\n") if r)
    return refs


def _ref_annotated_commit(workspace: Path, ref: str) -> str | None:
    """Return the commit SHA this notes ref is attached to, or None.

    Reads .git/refs/notes/.../<ref> tree to extract the single filename
    (the annotated commit). One subprocess per ref.
    """
    short = ref.replace("refs/notes/", "", 1)
    rc, out, _ = _git(workspace, "notes", f"--ref={short}", "list", timeout=5)
    if rc != 0 or not out.strip():
        return None
    # Output: "<note-blob-sha> <annotated-commit-sha>" (one line per ref)
    parts = out.strip().split("\n")[0].split()
    return parts[1] if len(parts) >= 2 else None


def _read_note_json(workspace: Path, ref: str) -> dict | None:
    """Read the JSON payload of a notes ref (the per-artifact note blob).

    Walks ref → notes-commit → tree → first blob. Returns parsed JSON or None.
    """
    rc, out, _ = _git(workspace, "cat-file", "-p", ref, timeout=10)
    if rc != 0:
        return None
    tree_sha = None
    for line in out.split("\n"):
        if line.startswith("tree "):
            tree_sha = line.split()[1]
            break
    if not tree_sha:
        return None
    rc, tree_out, _ = _git(workspace, "ls-tree", tree_sha, timeout=5)
    if rc != 0:
        return None
    # Tree line: "<mode> blob <sha>\t<filename>"
    blob_sha = None
    for line in tree_out.strip().split("\n"):
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "blob":
            blob_sha = parts[2]
            break
    if not blob_sha:
        return None
    rc, blob_out, _ = _git(workspace, "cat-file", "-p", blob_sha, timeout=5)
    if rc != 0:
        return None
    try:
        return json.loads(blob_out)
    except json.JSONDecodeError:
        return None


def _index_path(workspace: Path) -> Path:
    return workspace / ".empirica" / "cache" / "commit_artifact_index.json"


def _notes_dir(workspace: Path) -> Path:
    return workspace / ".git" / "refs" / "notes" / "empirica"


def _index_is_stale(workspace: Path) -> bool:
    cache = _index_path(workspace)
    notes = _notes_dir(workspace)
    if not cache.exists():
        return True
    if not notes.exists():
        return False
    return notes.stat().st_mtime > cache.stat().st_mtime


def _build_index(workspace: Path, verbose: bool = False) -> dict[str, list[dict]]:
    """Map commit SHA → list of artifact entries (type/ref/artifact_id/blob).

    Slow path: one subprocess per ref. Cached after first build.
    """
    refs = _list_refs(workspace)
    if verbose:
        print(f"[commit-context] indexing {len(refs)} refs…", flush=True)
    index: dict[str, list[dict]] = {}
    t0 = time.time()
    for i, ref in enumerate(refs, 1):
        parts = ref.split("/")
        if len(parts) < 5:
            continue
        artifact_type = parts[3]
        artifact_id = parts[4]
        commit_sha = _ref_annotated_commit(workspace, ref)
        if not commit_sha:
            continue
        index.setdefault(commit_sha, []).append({
            "type": artifact_type, "ref": ref, "artifact_id": artifact_id,
        })
        if verbose and i % 500 == 0:
            print(f"[commit-context] {i}/{len(refs)} ({time.time() - t0:.1f}s)", flush=True)
    return index


def _load_or_build_index(workspace: Path, force: bool = False, verbose: bool = False) -> dict[str, list[dict]]:
    cache = _index_path(workspace)
    if not force and not _index_is_stale(workspace):
        try:
            with cache.open() as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    index = _build_index(workspace, verbose=verbose)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with cache.open("w") as f:
            json.dump(index, f)
    except OSError:
        pass
    return index


def _resolve_targets(workspace: Path, args: argparse.Namespace) -> list[str]:
    """Resolve the target commit set from CLI args. Returns full SHA list."""
    if args.session:
        # All commits authored within the Empirica session window.
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        # session_id may be a prefix — expand to full id via a single lookup
        full_sid = args.session
        sess = db.get_session(full_sid)
        if not sess:
            # Try prefix match by listing recent and filtering
            return []
        start = sess.get("start_time")
        end = sess.get("end_time")
        if not start:
            return []
        since = datetime.fromtimestamp(start).isoformat()
        until = datetime.fromtimestamp(end).isoformat() if end else None
        return _commits_in_range(workspace, since=since, until=until)
    if args.range:
        rc, out, _ = _git(workspace, "rev-list", args.range)
        return [c for c in out.strip().split("\n") if c] if rc == 0 else []
    if args.since or args.until:
        return _commits_in_range(workspace, since=args.since, until=args.until)
    if args.commit:
        rc, out, _ = _git(workspace, "rev-parse", args.commit)
        return [out.strip()] if rc == 0 and out.strip() else []
    return []


def _commits_in_range(workspace: Path, since: str | None = None, until: str | None = None) -> list[str]:
    cmd = ["log", "--format=%H"]
    if since:
        cmd.append(f"--since={since}")
    if until:
        cmd.append(f"--until={until}")
    rc, out, _ = _git(workspace, *cmd)
    return [c for c in out.strip().split("\n") if c] if rc == 0 else []


def _commit_meta(workspace: Path, sha: str) -> dict:
    rc, out, _ = _git(workspace, "log", "-1", "--format=%H%x09%ci%x09%an%x09%s", sha)
    if rc != 0 or not out.strip():
        return {"sha": sha}
    parts = out.strip().split("\t", 3)
    return {
        "sha": parts[0] if len(parts) > 0 else sha,
        "date": parts[1] if len(parts) > 1 else "",
        "author": parts[2] if len(parts) > 2 else "",
        "subject": parts[3] if len(parts) > 3 else "",
    }


def _preview(payload: dict, artifact_type: str) -> str:
    """One-line preview from a note JSON payload.

    Notes use a top-level summary field plus a nested `<type>_data` block;
    fall through both before giving up.
    """
    if not payload:
        return ""
    keys_by_type = {
        "findings": ("finding",),
        "decisions": ("choice",),
        "dead_ends": ("approach",),
        "mistakes": ("mistake",),
        "unknowns": ("unknown",),
        "assumptions": ("assumption",),
        "goals": ("objective",),
        "cascades": ("intent", "trigger_command"),
    }
    nested_key_by_type = {
        "findings": "finding_data",
        "decisions": "decision_data",
        "dead_ends": "dead_end_data",
        "mistakes": "mistake_data",
        "unknowns": "unknown_data",
        "assumptions": "assumption_data",
        "goals": "goal_data",
        "cascades": "cascade_data",
    }
    candidates: list[dict] = [payload]
    nested = payload.get(nested_key_by_type.get(artifact_type, ""))
    if isinstance(nested, dict):
        candidates.append(nested)
    for source in candidates:
        for k in keys_by_type.get(artifact_type, ()):
            v = source.get(k)
            if v:
                v = str(v).replace("\n", " ").strip()
                return v[:120] + ("…" if len(v) > 120 else "")
    return ""


_NESTED_DATA_KEY = {
    "findings": "finding_data",
    "decisions": "decision_data",
    "dead_ends": "dead_end_data",
    "mistakes": "mistake_data",
    "unknowns": "unknown_data",
    "assumptions": "assumption_data",
    "goals": "goal_data",
    "cascades": "cascade_data",
}


def _build_id_lookup(index: dict[str, list[dict]]) -> dict[str, tuple[str, str]]:
    """Flatten the commit→artifacts index into artifact_id → (type, ref)."""
    lookup: dict[str, tuple[str, str]] = {}
    for entries in index.values():
        for e in entries:
            aid = e.get("artifact_id")
            if aid:
                lookup[aid] = (e["type"], e["ref"])
    return lookup


def _resolve_unknown_type_ref(workspace: Path, artifact_id: str) -> tuple[str, str] | None:
    """Probe each artifact namespace to find which holds this id. Last resort."""
    for ns in ARTIFACT_NAMESPACES:
        ref = f"refs/notes/empirica/{ns}/{artifact_id}"
        rc, _, _ = _git(workspace, "rev-parse", "--verify", ref, timeout=2)
        if rc == 0:
            return ns, ref
    return None


def _walk_edges(payload: dict, artifact_type: str) -> list[tuple[str, str | None, str]]:
    """Return outgoing edges from an artifact's note JSON.

    Each edge: (target_id, target_type_or_None, relation_label).
    Sources walked, in order:
      1. payload.<type>_data.edges[] from log-artifacts graph format
      2. payload.goal_id implicit pointer
      3. payload.subtask_id implicit pointer
      4. payload.<type>_data.parent_id (+ parent_type) sub-artifact hierarchy
    """
    edges: list[tuple[str, str | None, str]] = []
    if not isinstance(payload, dict):
        return edges
    nested_key = _NESTED_DATA_KEY.get(artifact_type, "")
    nested = payload.get(nested_key) if nested_key else None
    if not isinstance(nested, dict):
        nested = {}

    # 1. Graph-format edges
    raw_edges = nested.get("edges") or payload.get("edges") or []
    if isinstance(raw_edges, list):
        for ed in raw_edges:
            if not isinstance(ed, dict):
                continue
            to_id = ed.get("to")
            if to_id:
                edges.append((to_id, ed.get("to_type"), ed.get("relation", "related")))

    # 2-3. Implicit pointers
    goal_id = payload.get("goal_id") or nested.get("goal_id")
    if goal_id:
        edges.append((goal_id, "goals", "in_goal"))
    subtask_id = payload.get("subtask_id") or nested.get("subtask_id")
    if subtask_id:
        edges.append((subtask_id, "subtasks", "in_subtask"))

    # 4. Sub-artifact parent
    parent_id = nested.get("parent_id") or payload.get("parent_id")
    parent_type = nested.get("parent_type") or payload.get("parent_type")
    if parent_id:
        edges.append((parent_id, parent_type, "child_of"))
    return edges


def _walk_artifact_tree(workspace: Path, artifact_type: str, artifact_ref: str,
                        artifact_id: str, depth: int, max_depth: int,
                        visited: set[str], id_lookup: dict[str, tuple[str, str]],
                        ) -> dict | None:
    """Recursively walk edges from an artifact, returning a tree dict.

    Returns: {type, artifact_id, preview, depth, children: [tree...]} or None if cycle.
    """
    if artifact_id in visited:
        return None
    visited.add(artifact_id)

    payload = _read_note_json(workspace, artifact_ref) or {}
    node: dict[str, Any] = {
        "type": artifact_type,
        "artifact_id": artifact_id,
        "depth": depth,
        "preview": _preview(payload, artifact_type),
        "children": [],
    }
    if depth >= max_depth:
        return node

    for to_id, to_type, relation in _walk_edges(payload, artifact_type):
        # Resolve target's type+ref
        if to_type and to_type in ARTIFACT_NAMESPACES:
            target = (to_type, f"refs/notes/empirica/{to_type}/{to_id}")
        else:
            target = id_lookup.get(to_id) or _resolve_unknown_type_ref(workspace, to_id)
        if not target:
            # Unknown id — record stub so the edge isn't silent
            node["children"].append({
                "type": to_type or "unknown",
                "artifact_id": to_id,
                "depth": depth + 1,
                "preview": f"[unresolved · {relation}]",
                "children": [],
            })
            continue
        target_type, target_ref = target
        child = _walk_artifact_tree(workspace, target_type, target_ref, to_id,
                                    depth + 1, max_depth, visited, id_lookup)
        if child is not None:
            child["relation"] = relation
            node["children"].append(child)
    return node


def _format_tree(node: dict, indent: int = 0) -> list[str]:
    """Render a walked tree as indented lines."""
    lines: list[str] = []
    pad = "    " + "  " * indent
    aid = (node.get("artifact_id") or "")[:8]
    rel = node.get("relation")
    rel_str = f" ←{rel}" if rel else ""
    lines.append(f"{pad}└─ [{node['type']}/{aid}]{rel_str}  {node.get('preview', '')}")
    for child in node.get("children", []):
        lines.extend(_format_tree(child, indent + 1))
    return lines


def _format_commit_block(meta: dict, entries: list[dict], workspace: Path,
                          depth: int = 0,
                          id_lookup: dict[str, tuple[str, str]] | None = None) -> list[str]:
    lines: list[str] = []
    short_sha = meta["sha"][:8]
    subject = meta.get("subject", "")
    date = meta.get("date", "")
    lines.append(f"\n● {short_sha}  {date}  {subject}")
    if not entries:
        lines.append("    (no artifacts noted)")
        return lines
    grouped: dict[str, list[dict]] = {}
    for e in entries:
        grouped.setdefault(e["type"], []).append(e)
    for t in ARTIFACT_NAMESPACES:
        bucket = grouped.get(t, [])
        if not bucket:
            continue
        lines.append(f"  {t} ({len(bucket)}):")
        for e in bucket:
            payload = _read_note_json(workspace, e["ref"]) or {}
            created = payload.get("created_at", "")
            preview = _preview(payload, t)
            aid = (e.get("artifact_id") or "")[:8]
            lines.append(f"    [{aid}] {created[:19]}  {preview}")
            if depth > 0 and id_lookup is not None:
                # Walk this artifact's edges to depth N
                visited: set[str] = set()
                tree = _walk_artifact_tree(workspace, t, e["ref"],
                                            e["artifact_id"], 0, depth,
                                            visited, id_lookup)
                if tree and tree.get("children"):
                    for child in tree["children"]:
                        lines.extend(_format_tree(child, indent=0))
    return lines


def handle_commit_context_command(args: argparse.Namespace) -> dict:
    """CLI handler. Returns dict for --output json mode."""
    git_root = get_git_root()
    workspace = git_root if git_root else Path(".")
    if not (workspace / ".git").exists():
        msg = f"Not a git repository: {workspace}"
        if args.output == "json":
            return {"ok": False, "error": msg}
        print(msg)
        return {"ok": False, "error": msg}

    index = _load_or_build_index(workspace, force=args.rebuild_index, verbose=args.verbose)
    targets = _resolve_targets(workspace, args)

    if not targets:
        msg = "No target commits resolved. Provide a SHA, --range, --since, or --session."
        if args.output == "json":
            return {"ok": False, "error": msg}
        print(msg)
        return {"ok": False, "error": msg}

    id_lookup = _build_id_lookup(index) if args.depth > 0 else {}

    matches: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "ok": True,
        "index_size_commits": len(index),
        "target_commits": len(targets),
        "depth": args.depth,
        "matches": matches,
    }
    for sha in targets:
        entries = index.get(sha, [])
        meta = _commit_meta(workspace, sha)
        commit_artifacts: list[dict[str, Any]] = []
        for e in entries:
            entry: dict[str, Any] = {
                "type": e["type"],
                "artifact_id": e["artifact_id"],
            }
            if args.full:
                entry["ref"] = e["ref"]
                entry["payload"] = _read_note_json(workspace, e["ref"])
            if args.depth > 0:
                visited: set[str] = set()
                tree = _walk_artifact_tree(workspace, e["type"], e["ref"],
                                            e["artifact_id"], 0, args.depth,
                                            visited, id_lookup)
                if tree:
                    entry["tree"] = tree
            commit_artifacts.append(entry)
        matches.append({
            "commit": meta,
            "artifact_count": len(entries),
            "artifacts": commit_artifacts,
        })

    if args.output == "json":
        return result

    # Human output
    total_artifacts = sum(m["artifact_count"] for m in matches)
    matched_commits = sum(1 for m in matches if m["artifact_count"] > 0)
    print(f"Commit-context: {len(targets)} target commits, "
          f"{matched_commits} with artifacts, {total_artifacts} artifacts total "
          f"(index covers {len(index)} commits)")
    for sha in targets:
        entries = index.get(sha, [])
        if not entries and args.only_with_artifacts:
            continue
        meta = _commit_meta(workspace, sha)
        for line in _format_commit_block(meta, entries, workspace,
                                          depth=args.depth, id_lookup=id_lookup):
            print(line)
    # Return None for human mode so cli_core doesn't dump the dict
    return None  # type: ignore[return-value]


def add_commit_context_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "commit-context",
        help="Show artifacts (git notes under refs/notes/empirica/*) anchored to commits",
        description="Aggregates artifacts logged against commit(s) and outputs them grouped by type.",
    )
    p.add_argument("commit", nargs="?", help="Commit SHA or ref (default mode)")
    p.add_argument("--range", help="Git rev range, e.g. HEAD~10..HEAD")
    p.add_argument("--since", help="Date string (e.g. 2026-04-01) — uses git log --since")
    p.add_argument("--until", help="Date string — uses git log --until")
    p.add_argument("--session", help="Empirica session_id prefix — all commits in session window")
    p.add_argument("--depth", type=int, default=0, metavar="N",
                   help="Walk artifact graph edges to depth N (default: 0, no walk)")
    p.add_argument("--full", action="store_true",
                   help="Include full artifact JSON payloads in output")
    p.add_argument("--only-with-artifacts", action="store_true",
                   help="Skip commits that have no notes (human output only)")
    p.add_argument("--rebuild-index", action="store_true",
                   help="Force rebuild of the commit→artifact index cache")
    p.add_argument("--output", choices=("human", "json"), default="human")
    p.add_argument("--verbose", action="store_true", help="Show indexing progress")
    p.set_defaults(func=handle_commit_context_command)
