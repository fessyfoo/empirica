"""Bulk-project verbs (v0.5): projects-discover + projects-list.

projects-bulk-register lives in the same module once T2 ships — it consumes
the manifest produced here.

Filesystem walk strategy: walk roots ($HOME by default), skip well-known
noise dirs, look for any directory containing a `.empirica/` subdirectory.
For each match, parse `git remote get-url origin` (best-effort, normalize
ssh→https), and emit a manifest entry.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from empirica.cli.cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


DEFAULT_MANIFEST_PATH = Path.home() / ".empirica" / "discovered_projects.yaml"

SKIP_DIR_NAMES = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".tox",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist",
    "target", ".next", ".nuxt", ".cache", ".gradle", ".idea", ".vscode",
})


# ── Filesystem walk ─────────────────────────────────────────────────────


def _should_skip_dir(dirname: str, include_hidden: bool) -> bool:
    """Skip noise directories. Hidden dirs skipped unless --include-hidden."""
    if dirname in SKIP_DIR_NAMES:
        return True
    return not include_hidden and dirname.startswith(".") and dirname != "."


def _walk_for_empirica(root: Path, max_depth: int, include_hidden: bool) -> list[Path]:
    """Yield directories under `root` that contain `.empirica/project.yaml`.

    A project is identified by the presence of `.empirica/project.yaml`
    (real project metadata) rather than just an empty `.empirica/` directory.
    This handles the common workspace layout where a parent directory has a
    bare `.empirica/` and each sibling subdirectory is a real project.

    Walker keeps descending past matched projects in case there are nested
    sub-projects, but skips into the `.empirica/` directory itself (no
    point looking for projects inside another project's metadata folder).
    Skips SKIP_DIR_NAMES + hidden dirs (unless --include-hidden).
    """
    if not root.exists() or not root.is_dir():
        return []

    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        current, depth = stack.pop()

        # Real project: .empirica/project.yaml present
        if (current / ".empirica" / "project.yaml").is_file():
            found.append(current)
            # Still recurse — nested projects exist (rare but valid)

        if depth >= max_depth:
            continue

        try:
            children = list(current.iterdir())
        except (PermissionError, OSError) as e:
            logger.debug(f"projects-discover: skip {current} ({e})")
            continue

        for child in children:
            if not child.is_dir():
                continue
            if _should_skip_dir(child.name, include_hidden):
                continue
            # Don't descend into .empirica/ itself — internal data, no sub-projects
            if child.name == ".empirica":
                continue
            stack.append((child, depth + 1))

    return found


# ── Git remote parsing ──────────────────────────────────────────────────


_SSH_REMOTE_RE = re.compile(r"^git@([^:]+):(.+?)(\.git)?$")
_HTTPS_REMOTE_RE = re.compile(r"^(https?://[^/]+/.+?)(\.git)?$")


def _normalize_remote_url(raw: str) -> str | None:
    """Convert ssh-form remotes to https-form. Pass through https-form. None on garbage.

    Examples:
      git@github.com:Nubaeon/empirica.git → https://github.com/Nubaeon/empirica
      https://github.com/Nubaeon/empirica.git → https://github.com/Nubaeon/empirica
    """
    if not raw:
        return None
    raw = raw.strip()
    m = _SSH_REMOTE_RE.match(raw)
    if m:
        host, path = m.group(1), m.group(2)
        return f"https://{host}/{path}"
    m = _HTTPS_REMOTE_RE.match(raw)
    if m:
        return m.group(1)
    return None


def _read_git_remote_raw(project_path: Path) -> str | None:
    """Best-effort raw `git remote get-url origin` from project_path. None on miss/error.

    Returns the unmodified remote URL string. Caller normalizes to https-form
    via _normalize_remote_url so the manifest can carry both shapes.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.debug(f"projects-discover: git remote read failed for {project_path}: {e}")
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    return raw or None


# ── Manifest building ───────────────────────────────────────────────────


def _build_manifest_entry(project_path: Path) -> dict[str, Any]:
    """Build one manifest entry for a discovered project."""
    raw_remote = _read_git_remote_raw(project_path)
    return {
        "path": str(project_path.resolve()),
        "name": project_path.name,
        "repo_url": _normalize_remote_url(raw_remote) if raw_remote else None,
        "has_empirica_dir": True,
        "git_remote_origin": raw_remote,
    }


def discover_projects(
    roots: list[Path] | None = None,
    *,
    max_depth: int = 5,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """Walk roots and build a manifest of discovered .empirica/ projects.

    Public function — callable from tests and other modules without going
    through the CLI handler.
    """
    if roots is None:
        roots = [Path.home()]

    seen: set[str] = set()
    projects: list[dict[str, Any]] = []
    for root in roots:
        for project_path in _walk_for_empirica(root, max_depth, include_hidden):
            resolved = str(project_path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            projects.append(_build_manifest_entry(project_path))

    projects.sort(key=lambda p: p["path"])

    return {
        "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roots": [str(r) for r in roots],
        "projects": projects,
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write the manifest as YAML to `path`. Creates parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def load_manifest(path: Path) -> dict[str, Any] | None:
    """Load a previously-written manifest. Returns None if missing/unparseable."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        logger.debug(f"projects-list: failed to load manifest {path}: {e}")
        return None


# ── Output formatters ──────────────────────────────────────────────────


def _format_table(manifest: dict[str, Any]) -> str:
    projects = manifest.get("projects", [])
    if not projects:
        return "No Empirica projects discovered."

    name_w = max(len("NAME"), max(len(p.get("name") or "") for p in projects))
    path_w = max(len("PATH"), min(60, max(len(p.get("path") or "") for p in projects)))
    repo_w = max(len("REPO"), max(len(p.get("repo_url") or "") for p in projects))

    lines = [f"{'NAME':<{name_w}}  {'PATH':<{path_w}}  {'REPO':<{repo_w}}"]
    lines.append("-" * (name_w + path_w + repo_w + 4))
    for p in projects:
        name = (p.get("name") or "").ljust(name_w)
        path = (p.get("path") or "")[:path_w].ljust(path_w)
        repo = (p.get("repo_url") or "—").ljust(repo_w)
        lines.append(f"{name}  {path}  {repo}")
    lines.append("")
    lines.append(f"{len(projects)} projects discovered (manifest: {manifest.get('discovered_at', '?')}).")
    return "\n".join(lines)


def _format_output(manifest: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(manifest, indent=2)
    if output_format == "table":
        return _format_table(manifest)
    return yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ── Command handlers ───────────────────────────────────────────────────


def handle_projects_discover_command(args) -> None:
    """Handle the projects-discover command."""
    try:
        roots = [Path(r).expanduser() for r in (args.roots or [str(Path.home())])]
        manifest = discover_projects(
            roots=roots,
            max_depth=args.max_depth,
            include_hidden=args.include_hidden,
        )

        # Determine target path
        manifest_arg = getattr(args, "manifest", None)
        if manifest_arg == "-":
            target = None
        elif manifest_arg:
            target = Path(manifest_arg).expanduser()
        else:
            target = DEFAULT_MANIFEST_PATH

        # Always print to stdout in chosen format
        output_format = getattr(args, "output", "yaml")
        if output_format == "yaml":
            sys.stdout.write(_format_output(manifest, "yaml"))
        elif output_format == "json":
            sys.stdout.write(_format_output(manifest, "json") + "\n")

        # Write to manifest file unless stdout-only
        if target is not None:
            try:
                write_manifest(manifest, target)
                if output_format != "yaml":
                    # Already printed JSON to stdout; also confirm cache write on stderr
                    print(
                        f"\n📁 Manifest cached at {target} ({len(manifest['projects'])} projects)",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"\n# Manifest cached at {target} ({len(manifest['projects'])} projects)",
                        file=sys.stderr,
                    )
            except OSError as e:
                print(f"⚠ Failed to write manifest cache: {e}", file=sys.stderr)
    except Exception as e:
        handle_cli_error(e, "projects-discover")


def handle_projects_list_command(args) -> None:
    """Handle the projects-list command."""
    try:
        manifest_arg = getattr(args, "manifest", None)
        target = Path(manifest_arg).expanduser() if manifest_arg else DEFAULT_MANIFEST_PATH

        manifest = None if args.refresh else load_manifest(target)
        if manifest is None:
            # Fall back to fresh scan
            manifest = discover_projects(roots=[Path.home()])
            # Best-effort cache write
            try:
                write_manifest(manifest, target)
            except OSError as e:
                logger.debug(f"projects-list: cache write failed: {e}")

        sys.stdout.write(_format_output(manifest, getattr(args, "output", "table")))
        if getattr(args, "output", "table") == "table":
            sys.stdout.write("\n")
    except Exception as e:
        handle_cli_error(e, "projects-list")
