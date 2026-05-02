"""StatuslinePanel widget — empirica chat header strip showing epistemic state.

Per CHAT.md spec: condensed 1-line panel below the Header showing
phase + key vectors + open goals/unknowns counts. /statusline command
cycles modes (basic | default | learning | full) at the same fidelity
the CC plugin's statusline_empirica.py exposes.

Phase 6b: rendering routed through `empirica.core.statusline` (the
shared module shared with the CC plugin). Same color tiers, same
emoji palette, same delta semantics. Backend is RichBackend so
Textual/Rich markup interprets the styling correctly.

Data path (unchanged from Phase 6):
  - empirica.utils.session_resolver.get_instance_id  (current instance)
  - empirica.core.cockpit.enrichment.statusline_summary  (live vectors)

Refreshes on a 2s tick to match cockpit_app's REFRESH_SECONDS. When no
live transaction state is available (e.g., chat launched without prior
empirica activity in the project), renders a muted placeholder.
"""

from __future__ import annotations

from textual.widgets import Static

from empirica.core.statusline import (
    RichBackend,
    calculate_confidence,
    format_confidence,
    format_open_counts,
    format_vector_colored,
)
from empirica.core.statusline.renderers import render_default_line

# Order matters — cycling /statusline goes through these in sequence.
RENDER_MODES = ("basic", "default", "learning", "full")

_BACKEND = RichBackend()

# Vector → display label mappings. Two-letter labels avoid the
# context/clarity/completion collision that single-letter abbrevs hit.
_LEARNING_LABELS: tuple[tuple[str, str], ...] = (
    ("know", "K"),
    ("uncertainty", "U"),
    ("context", "Cx"),
    ("clarity", "Cl"),
)
_FULL_LABELS: tuple[tuple[str, str], ...] = (
    ("know", "K"),
    ("uncertainty", "U"),
    ("context", "Cx"),
    ("clarity", "Cl"),
    ("completion", "Cm"),
)


class StatuslinePanel(Static):
    """One-line statusline strip rendered just below the Header."""

    DEFAULT_CSS = """
    StatuslinePanel {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $primary;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("(statusline loading…)", id="chat-statusline", **kwargs)
        self._mode: str = "default"

    def cycle_mode(self) -> str:
        """Advance to the next render mode; returns the new mode name."""
        idx = RENDER_MODES.index(self._mode) if self._mode in RENDER_MODES else 0
        self._mode = RENDER_MODES[(idx + 1) % len(RENDER_MODES)]
        self.refresh_now()
        return self._mode

    def set_mode(self, mode: str) -> bool:
        if mode not in RENDER_MODES:
            return False
        self._mode = mode
        self.refresh_now()
        return True

    def current_mode(self) -> str:
        return self._mode

    def refresh_now(self) -> None:
        """Pull a fresh statusline summary and update the widget body."""
        body = self._build_text()
        self.update(body)

    def _build_text(self) -> str:
        """Build the line based on current mode."""
        try:
            from empirica.core.cockpit.enrichment import statusline_summary
            from empirica.utils.session_resolver import (
                InstanceResolver,
                get_instance_id,
            )
        except Exception as e:  # noqa: BLE001 — surface if empirica internals shift
            return f"[dim]statusline unavailable: {type(e).__name__}[/dim]"

        instance_id = get_instance_id()
        if not instance_id:
            return "[dim]no instance_id (chat not bound to empirica session)[/dim]"

        project_path = None
        session_id = None
        try:
            resolver = InstanceResolver()
            project_path = str(resolver.project_path()) if hasattr(resolver, "project_path") else None
            # Try to resolve current session — may not exist if chat hasn't
            # opened a transaction yet (Phase 6 v1 doesn't auto-PREFLIGHT).
            if hasattr(resolver, "session_id"):
                try:
                    session_id = resolver.session_id()
                except Exception:  # noqa: BLE001
                    session_id = None
        except Exception:  # noqa: BLE001
            pass

        try:
            summary = statusline_summary(
                instance_id=instance_id,
                label_fallback=None,
                project_path=project_path,
                session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001
            return f"[dim]statusline error: {type(e).__name__}[/dim]"

        return self._format_summary(summary)

    def _format_summary(self, summary) -> str:
        """Render based on _mode using the shared statusline package."""
        if not getattr(summary, "found", False):
            return "[dim]· no active transaction · use /preflight to start tracking[/dim]"

        # Marshal summary fields into the dict shapes the shared
        # renderers expect (same shapes the CC plugin uses).
        vectors: dict[str, float] = {}
        for k in (
            "know", "uncertainty", "context", "clarity",
            "coherence", "signal", "density",
            "state", "change", "completion", "impact",
        ):
            v = getattr(summary, k, None)
            if v is not None:
                vectors[k] = v
        open_counts = {
            "open_goals": getattr(summary, "open_goals", None) or 0,
            "open_unknowns": getattr(summary, "open_unknowns", None) or 0,
            "goal_linked_unknowns": getattr(summary, "goal_linked_unknowns", None) or 0,
        }
        # Use confidence from summary if present, else compute from vectors
        conf = getattr(summary, "confidence", None)
        if conf is None:
            conf = calculate_confidence(vectors)

        if self._mode == "basic":
            return format_confidence(conf, backend=_BACKEND)

        if self._mode == "default":
            return render_default_line(
                vectors=vectors,
                open_counts=open_counts,
                backend=_BACKEND,
            )

        if self._mode == "learning":
            parts = [format_open_counts(open_counts, backend=_BACKEND)]
            for k, lbl in _LEARNING_LABELS:
                v = vectors.get(k)
                if v is not None:
                    parts.append(format_vector_colored(lbl, v, backend=_BACKEND))
            return " │ ".join(parts)

        # full mode — confidence + counts + all key vectors + extras
        parts = [
            format_confidence(conf, backend=_BACKEND),
            format_open_counts(open_counts, backend=_BACKEND),
        ]
        for k, lbl in _FULL_LABELS:
            v = vectors.get(k)
            if v is not None:
                parts.append(format_vector_colored(lbl, v, backend=_BACKEND))
        if getattr(summary, "artifact_count", None) is not None:
            parts.append(f"artifacts {summary.artifact_count}")
        return " │ ".join(parts)
