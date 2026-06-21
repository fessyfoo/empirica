"""Pre/post compact lifecycle hooks for empirica chat (Phase 10).

Mirrors the Claude Code plugin's compact hook pattern. When the user
runs /compact (or auto-trigger fires at 90% token usage in Phase 9):

  pre-compact  → save_breadcrumb()   writes session state to YAML
  user trims context (manual or driven by token bar)
  post-compact → load_breadcrumb()   reads YAML back
                  format_recovery_message() builds the system note
                  ChatApp installs that as a fresh SystemTurn

State captured:
  - active provider name + model
  - autonomy mode + statusline mode
  - last N agent/user turns (text only — full jsonl is on disk anyway)
  - session_id, written_at timestamp

The breadcrumb file lives at ~/.empirica/chat_breadcrumbs/{session_id}.yaml.

YAML rather than JSON because that's what CC's compact hook uses, and
because the file is human-readable for debugging when something goes
wrong with restoration.

This module is pure-data; ChatApp owns the lifecycle wiring.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _breadcrumb_root() -> Path:
    return Path.home() / ".empirica" / "chat_breadcrumbs"


@dataclass
class Breadcrumb:
    """Snapshot of chat state captured at pre-compact time."""

    session_id: str
    written_at_iso: str
    provider_name: str | None
    model: str | None
    autonomy_mode: str
    statusline_mode: str
    recent_turns: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Breadcrumb:
        return cls(
            session_id=data.get("session_id", ""),
            written_at_iso=data.get("written_at_iso", ""),
            provider_name=data.get("provider_name"),
            model=data.get("model"),
            autonomy_mode=data.get("autonomy_mode", "assistant"),
            statusline_mode=data.get("statusline_mode", "default"),
            recent_turns=list(data.get("recent_turns") or []),
        )


def _format_yaml(data: dict[str, Any]) -> str:
    """Minimal YAML emitter — avoids the optional pyyaml dependency.

    Only handles the shape we emit (str/int/None scalars + list-of-dicts
    for recent_turns). Falls back to JSON-as-block-scalar for the turns
    so we don't have to escape every character.
    """
    import json

    lines: list[str] = []
    for k, v in data.items():
        if k == "recent_turns":
            lines.append("recent_turns: |")
            for line in json.dumps(v, indent=2, ensure_ascii=False).splitlines():
                lines.append("  " + line)
        elif v is None:
            lines.append(f"{k}: ~")
        elif isinstance(v, str):
            # quote if it has special chars
            if any(c in v for c in ":#'\"\n"):
                escaped = v.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{k}: "{escaped}"')
            else:
                lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


def _parse_yaml(content: str) -> dict[str, Any]:
    """Minimal YAML parser — inverse of _format_yaml."""
    import json

    out: dict[str, Any] = {}
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line == "recent_turns: |":
            # collect indented block, strip 2-space indent, parse JSON
            block: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                block.append(lines[i][2:] if lines[i].startswith("  ") else "")
                i += 1
            try:
                out["recent_turns"] = json.loads("\n".join(block) or "[]")
            except json.JSONDecodeError:
                out["recent_turns"] = []
            continue
        # scalar
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "~":
            out[key] = None
        elif val.startswith('"') and val.endswith('"'):
            inner = val[1:-1]
            out[key] = inner.replace('\\"', '"').replace("\\\\", "\\")
        else:
            out[key] = val
        i += 1
    return out


def save_breadcrumb(
    *,
    session_id: str,
    provider_name: str | None,
    model: str | None,
    autonomy_mode: str,
    statusline_mode: str,
    recent_turns: list[dict[str, Any]] | None = None,
    root: Path | None = None,
) -> Path:
    """Write a breadcrumb YAML to ~/.empirica/chat_breadcrumbs/{session_id}.yaml.

    Returns the file path written. Existing files are overwritten —
    one breadcrumb per session at any time (the post-compact reads
    the most recent state).
    """
    bc = Breadcrumb(
        session_id=session_id,
        written_at_iso=time.strftime("%Y-%m-%dT%H:%M:%S%z") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        provider_name=provider_name,
        model=model,
        autonomy_mode=autonomy_mode,
        statusline_mode=statusline_mode,
        recent_turns=list(recent_turns or []),
    )
    root = root or _breadcrumb_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{session_id}.yaml"
    path.write_text(_format_yaml(asdict(bc)))
    return path


def load_breadcrumb(session_id: str, root: Path | None = None) -> Breadcrumb | None:
    """Read the breadcrumb for a session, or None if it doesn't exist."""
    root = root or _breadcrumb_root()
    path = root / f"{session_id}.yaml"
    if not path.exists():
        return None
    try:
        return Breadcrumb.from_dict(_parse_yaml(path.read_text()))
    except Exception:
        return None


def format_recovery_message(bc: Breadcrumb) -> str:
    """Build the post-compact SystemTurn body that re-orients the AI.

    Includes everything needed to resume: provider+model, autonomy mode,
    a brief turn-tail summary, and a pointer to the full session jsonl
    if the AI needs deeper context.
    """
    lines = [
        "▶ post-compact recovery",
        f"session: {bc.session_id[:8]} (resumed at {bc.written_at_iso})",
        f"provider: {bc.provider_name or '(none)'} · model: {bc.model or '(none)'}",
        f"autonomy: {bc.autonomy_mode} · statusline: {bc.statusline_mode}",
    ]
    if bc.recent_turns:
        lines.append("recent context:")
        for t in bc.recent_turns[-5:]:  # cap at last 5 even if more saved
            kind = t.get("kind", "?")
            text = (t.get("text") or "").strip().replace("\n", " ")
            if len(text) > 80:
                text = text[:77] + "…"
            lines.append(f"  · {kind}: {text}")
        lines.append(f"(full jsonl at ~/.empirica/chat_sessions/{bc.session_id}.jsonl)")
    return "\n".join(lines)
